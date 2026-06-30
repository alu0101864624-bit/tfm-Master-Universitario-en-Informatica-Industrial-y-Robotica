import network
import time
import ubinascii
import uhashlib
import urequests
import json
import machine
import gc
import urandom
from umqtt.simple import MQTTClient

# --- CONFIGURACIONES DE ENTORNO ---
WIFI_SSID = ""
WIFI_PASS = ""
MQTT_SERVER = "192.168.1.130"
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
CLIENT_ID = ubinascii.hexlify(machine.unique_id())
INTERVALO_CICLO_SEG = 30  # Frecuencia de actualización física del gemelo

# Credenciales de tu sensor Tuya Físico
ACCESS_ID = ""
ACCESS_SECRET = "" 
DEVICE_ID = ""
TUYA_HOST = "https://openapi.tuyaeu.com"

# Variables de caché para Token
token_guardado = None
ultimo_registro_token = 0

# --- NUEVO: SISTEMA STORE AND FORWARD (BÚFER LOCAL) ---
buffer_offline = []
MAX_BUFFER_SIZE = 100  # Límite de tramas a guardar en RAM para no agotar la memoria

# --- INVENTARIO ESTRUCTURADO DE LA FLOTA CANARIA ---
INVENTARIO_SENSORES = [
    {"id_dispositivo": "col_clima_int_01", "tipo_sensor": "temperatura_interior", "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"},
    {"id_dispositivo": "col_clima_ext_01", "tipo_sensor": "temperatura_exterior", "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Patio"},
    {"id_dispositivo": "col_potencia_01", "tipo_sensor": "potencia", "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Cuadro_General"},
    {"id_dispositivo": "col_caudal_01", "tipo_sensor": "caudal", "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Entrada_Agua"},
    {"id_dispositivo": "hosp_clima_int_01", "tipo_sensor": "temperatura_interior", "isla": "Gran_Canaria", "municipio": "Las_Palmas", "tipo_instalacion": "Hospital", "centro": "Insular", "zona": "Urgencias"},
    {"id_dispositivo": "hosp_potencia_01", "tipo_sensor": "potencia", "isla": "Gran_Canaria", "municipio": "Las_Palmas", "tipo_instalacion": "Hospital", "centro": "Insular", "zona": "Quirofano_1"},
    {"id_dispositivo": "edar_caudal_01", "tipo_sensor": "caudal", "isla": "Fuerteventura", "municipio": "Puerto_Rosario", "tipo_instalacion": "Depuradora", "centro": "EDAR_Norte", "zona": "Bombeo_Entrada"}
]

# ==========================================================================
# 📊 MOTORES DE COMPORTAMIENTO FÍSICO (GEMELOS DIGITALES)
# ==========================================================================

class GemeloTermico:
    def __init__(self, t_inicial, alpha, beta):
        self.t_actual = t_inicial
        self.alpha = alpha  
        self.beta = beta    

    def computar_estado(self, t_real, hora, anomalia=False):
        q_ocu = 0.15 if (8 <= hora <= 14) else 0.0
        
        if anomalia:
            self.alpha = 0.45  
            q_ocu = -0.6       
            
        gradiente = t_real - self.t_actual
        self.t_actual += (self.alpha * gradiente) + (self.beta * q_ocu)
        
        ruido = ((urandom.getrandbits(8) / 255.0) - 0.5) * 0.04
        return round(self.t_actual + ruido, 2)


class GemeloElectrico:
    def __init__(self, p_base, gamma):
        self.p_base = p_base
        self.gamma = gamma  

    def computar_estado(self, t_int_sim, hora, anomalia=False):
        base_actual = self.p_base * (1.3 if (7 <= hora <= 18) else 0.4)
        desviacion_termica = abs(t_int_sim - 21.0)
        p_hvac = desviacion_termica * self.gamma
        
        if anomalia:
            return round((base_actual + p_hvac) * 2.3, 1)
            
        return round(base_actual + p_hvac, 1)


class GemeloHidraulico:
    def __init__(self, caudal_nominal):
        self.caudal_nominal = caudal_nominal

    def computar_estado(self, hora, anomalia=False):
        if (8 <= hora <= 9) or (13 <= hora <= 14):
            base = self.caudal_nominal * 0.85
        elif (10 <= hora <= 17):
            base = self.caudal_nominal * 0.25
        else:
            base = 0.0 
            
        if anomalia:
            return round(base + (self.caudal_nominal * 1.5), 2)
            
        fluctuacion_presion = ((urandom.getrandbits(8) / 255.0) - 0.5) * 0.15
        return round(max(0.0, base + fluctuacion_presion), 2)

motores = {
    "col_clima_int_01": GemeloTermico(t_inicial=21.0, alpha=0.05, beta=1.2),
    "hosp_clima_int_01": GemeloTermico(t_inicial=22.5, alpha=0.02, beta=0.8), 
    "col_potencia_01": GemeloElectrico(p_base=3000.0, gamma=450.0),
    "hosp_potencia_01": GemeloElectrico(p_base=8000.0, gamma=600.0),
    "col_caudal_01": GemeloHidraulico(caudal_nominal=25.0),
    "edar_caudal_01": GemeloHidraulico(caudal_nominal=120.0)
}

# ==========================================================================
# 🔐 INFRAESTRUCTURA DE PETICIONES SEGURO-CRIPTOGRÁFICAS TUYA
# ==========================================================================

def hmac_sha256(key, msg):
    key_bytes, msg_bytes = key.encode('utf-8'), msg.encode('utf-8')
    if len(key_bytes) > 64: key_bytes = uhashlib.sha256(key_bytes).digest()
    if len(key_bytes) < 64: key_bytes += b'\x00' * (64 - len(key_bytes))
    o_key_pad = bytes(b ^ 0x5c for b in key_bytes)
    i_key_pad = bytes(b ^ 0x36 for b in key_bytes)
    inner = uhashlib.sha256(i_key_pad + msg_bytes).digest()
    return ubinascii.hexlify(uhashlib.sha256(o_key_pad + inner).digest()).decode('utf-8').upper()

def generar_firma_tuya(method, url_path, token="", body_str=""):
    hora_utc = time.time() - 3600
    t = str(int((hora_utc + 946684800) * 1000))
    h = uhashlib.sha256(body_str.encode('utf-8'))
    body_hash = ubinascii.hexlify(h.digest()).decode('utf-8').lower()
    string_a_firmar = ACCESS_ID + token + t + method + "\n" + body_hash + "\n\n" + url_path
    return hmac_sha256(ACCESS_SECRET, string_a_firmar), t

def obtener_token_tuya():
    global token_guardado, ultimo_registro_token
    ahora = time.time()
    if token_guardado and (ahora - ultimo_registro_token < 3000):
        return token_guardado
    
    url_path = "/v1.0/token?grant_type=1"
    sign, t = generar_firma_tuya("GET", url_path)
    try:
        res = urequests.get(TUYA_HOST + url_path, headers={"client_id": ACCESS_ID, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"})
        js = res.json()
        res.close()
        if js.get("success"):
            token_guardado = js["result"]["access_token"]
            ultimo_registro_token = ahora
            return token_guardado
    except:
        return None
    return None

def obtener_temperatura_real(token):
    if not token: return None
    url_path = f"/v1.0/devices/{DEVICE_ID}/status"
    sign, t = generar_firma_tuya("GET", url_path, token=token)
    try:
        res = urequests.get(TUYA_HOST + url_path, headers={"client_id": ACCESS_ID, "access_token": token, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"})
        js = res.json()
        res.close()
        if js.get("success"):
            for item in js["result"]:
                if item["code"] == "va_temperature":
                    return item["value"] * 0.1 
    except:
        return None
    return None

# ==========================================================================
# 🚀 CICLO DE EJECUCIÓN CIENTÍFICO
# ==========================================================================

def main():
    print("🤖 INICIANDO ENTORNO DE GEMELO DIGITAL CON BASE FÍSICA COHERENTE")
    
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    
    timeout = 0
    while not wlan.isconnected() and timeout < 10:
        time.sleep(1)
        timeout += 1

    cliente_mqtt = None
    temp_ultimas = {"col_clima_int_01": 21.0, "hosp_clima_int_01": 22.0}

    while True:
        gc.collect()
        
        # --- 🛡️ SISTEMA DE RECONEXIÓN AUTOMÁTICA (WATCHDOG) ---
        # 1. Comprobar y restaurar Wi-Fi
        if not wlan.isconnected():
            print("⚠️ [Red] Wi-Fi desconectado. Intentando reconectar...")
            wlan.connect(WIFI_SSID, WIFI_PASS)
            timeout_wifi = 0
            while not wlan.isconnected() and timeout_wifi < 10:
                time.sleep(1)
                timeout_wifi += 1
            if wlan.isconnected():
                print("✅ [Red] Wi-Fi restaurado.")
                cliente_mqtt = None # Forzamos reiniciar MQTT porque la IP pudo cambiar
            else:
                print("❌ [Red] Imposible conectar al Wi-Fi. Operando offline.")

        # 2. Comprobar y restaurar MQTT
        if wlan.isconnected() and cliente_mqtt is None:
            try:
                print("🔄 [MQTT] Reconectando al broker...")
                cliente_mqtt = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS, keepalive=60)
                cliente_mqtt.connect()
                print("✅ [MQTT] Conexión establecida.")
            except Exception as e:
                print("❌ [MQTT] Error de conexión:", e)
                cliente_mqtt = None

        # --- FIN DEL WATCHDOG ---

        token = obtener_token_tuya()
        t_real_ancla = obtener_temperatura_real(token)
        
        if t_real_ancla is None:
            t_real_ancla = 22.4 
            print("⚠️ Conexión Tuya caída. Usando ancla térmica histórica de respaldo.")

        hora_solar = (int(time.time() / 3600) % 24)
        timestamp_epoch = int(time.time() + 946684800)

        print(f"\n🌍 [Sincronización Sensor Real] Baseline Térmico Tuya: {t_real_ancla} °C")

        payload_real = {
            "metadatos": {
                "id_dispositivo": "sensor_tuya_real",
                "tipo_sensor": "temperatura_interior",
                "isla": "Tenerife", 
                "municipio": "La_Orotava", 
                "tipo_instalacion": "Referencia", 
                "centro": "Habitacion_Fisica", 
                "zona": "Control_Tuya"
            },
            "timestamp_local": timestamp_epoch,
            "datos_sensores": {"temperatura_int_C": t_real_ancla},
            "ground_truth": {"estado_anomalo": False, "etiqueta_clase": "NOMINAL"}
        }
        
        topic_real = b"canarias/Referencia/Tenerife/La_Orotava/Habitacion_Fisica/Control_Tuya/sensor_tuya_real/datos"
        
        try:
            if cliente_mqtt:
                cliente_mqtt.publish(topic_real, json.dumps(payload_real).encode('utf-8'))
                print(f"✅ [Sensor Real Tuya] Dato original enviado a Node-RED ({t_real_ancla} °C)")
        except:
            pass

        for sensor in INVENTARIO_SENSORES:
            id_disp = sensor["id_dispositivo"]
            tipo = sensor["tipo_sensor"]
            
            forzar_falla = (urandom.getrandbits(8) % 100) < 6
            tipo_falla = "NOMINAL"

            valor_calculado = 0.0

            if tipo == "temperatura_interior":
                if forzar_falla: tipo_falla = "ANOMALIA_VENTANA_ABIERTA"
                valor_calculado = motores[id_disp].computar_estado(t_real_ancla, hora_solar, anomalia=forzar_falla)
                temp_ultimas[id_disp] = valor_calculado
                datos_payload = {"temperatura_int_C": valor_calculado}

            elif tipo == "temperatura_exterior":
                valor_calculado = round(t_real_ancla - 1.5 + ((urandom.getrandbits(8)/255.0)*0.4), 2)
                datos_payload = {"temperatura_ext_C": valor_calculado}

            elif tipo == "potencia":
                if forzar_falla: tipo_falla = "ANOMALIA_SOBRECARGA_DERIVACION"
                ref_termica = temp_ultimas["col_clima_int_01"] if "col" in id_disp else temp_ultimas["hosp_clima_int_01"]
                valor_calculado = motores[id_disp].computar_estado(ref_termica, hora_solar, anomalia=forzar_falla)
                datos_payload = {"potencia_W": valor_calculado}

            elif tipo == "caudal":
                if forzar_falla: tipo_falla = "ANOMALIA_FUGA_SOSTENIDA"
                valor_calculado = motores[id_disp].computar_estado(hora_solar, anomalia=forzar_falla)
                datos_payload = {"caudal_Lmin": valor_calculado}

            payload = {
                "metadatos": sensor,
                "timestamp_local": timestamp_epoch,
                "datos_sensores": datos_payload,
                "ground_truth": {
                    "estado_anomalo": forzar_falla,
                    "etiqueta_clase": tipo_falla
                }
            }

            topic = "canarias/{}/{}/{}/{}/{}/{}/datos".format(
                sensor["tipo_instalacion"], sensor["isla"], sensor["municipio"],
                sensor["centro"], sensor["zona"], id_disp
            ).encode('utf-8')

            mensaje_json = json.dumps(payload)
            
            # Intento de vaciado del búfer
            if cliente_mqtt:
                try:
                    while len(buffer_offline) > 0:
                        msg_perdido = buffer_offline[0]
                        cliente_mqtt.publish(msg_perdido['topic'], msg_perdido['payload'])
                        buffer_offline.pop(0) 
                        print("✅ [Recuperado] Trama enviada desde el búfer local.")
                        time.sleep(0.1) 
                except:
                    cliente_mqtt = None # Si falla el búfer, marcamos como caído

            # Intento de envío de la trama actual
            try:
                if cliente_mqtt:
                    cliente_mqtt.publish(topic, mensaje_json.encode('utf-8'))
                    print(f"✅ [Gemelo Físico] {id_disp} publicado ({tipo_falla})")
                else: 
                    raise Exception("Broker desconectado")
            except Exception as e:
                print(f"⚠️ [Fallo de Red] Conexión perdida. Guardando {id_disp} en búfer...")
                cliente_mqtt = None # Destruimos el objeto para forzar reconexión en el siguiente ciclo
                
                if len(buffer_offline) >= MAX_BUFFER_SIZE:
                    print("🚨 [Búfer Lleno] Descartando la trama más antigua.")
                    buffer_offline.pop(0) 
                buffer_offline.append({'topic': topic, 'payload': mensaje_json.encode('utf-8')})

            time.sleep(0.2)  

        time.sleep(INTERVALO_CICLO_SEG)

if __name__ == "__main__":
    main()