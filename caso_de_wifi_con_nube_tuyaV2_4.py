import network
import time
import ubinascii
import uhashlib
import urequests
import json
import machine
import ntptime
import gc
import urandom
from umqtt.simple import MQTTClient

# --- 1. CONFIGURACIÓN DE RED LOCAL ---
WIFI_SSID = ""
WIFI_PASS = ""

# --- 2. CONFIGURACIÓN MQTT ---
MQTT_SERVER = "192.168.1.130"
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
CLIENT_ID = ubinascii.hexlify(machine.unique_id())
INTERVALO_PRUEBAS_SEG = 30

# --- 3. CREDENCIALES TUYA CLOUD ---
ACCESS_ID = ""
ACCESS_SECRET = "" 
DEVICE_ID = ""
TUYA_HOST = "https://openapi.tuyaeu.com"

# ==========================================
# 🔐 VARIABLES GLOBALES PARA SISTEMA DE TOKEN CACHEADO
# Evitan pedir un token nuevo cada 30 segundos (Tuya bloquea por saturación)
# ==========================================
token_guardado = None
ultimo_registro_token = 0

# ==========================================
# 🌟 INVENTARIO DE FLOTA (Configuración Centralizada)
# Todos los sensores apuntan a la misma Aula Principal
# ==========================================
INVENTARIO_SENSORES = [
    {
        "id_dispositivo": "tuya_real_01",
        "tipo_sensor": "hibrido_tuya",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"
    },
    {
        "id_dispositivo": "sim_clima_int_01",
        "tipo_sensor": "clima_interior",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"
    },
    {
        "id_dispositivo": "sim_clima_ext_01",
        "tipo_sensor": "clima_exterior",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"
    },
    {
        "id_dispositivo": "sim_energia_01",
        "tipo_sensor": "energia",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"
    },
    {
        "id_dispositivo": "sim_agua_01",
        "tipo_sensor": "agua",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"
    }
]

# Diccionario para guardar el estado independiente de cada sensor simulado
estados_simulacion = {}

def inicializar_estados_simulacion():
    for sensor in INVENTARIO_SENSORES:
        estados_simulacion[sensor["id_dispositivo"]] = {
            "temp_int": 20.0 + (urandom.getrandbits(8) / 255.0 * 4.0), 
            "hum_int": 45.0 + (urandom.getrandbits(8) / 255.0 * 15.0),
            "temp_ext": 18.0 + (urandom.getrandbits(8) / 255.0 * 10.0), 
            "hum_ext": 60.0 + (urandom.getrandbits(8) / 255.0 * 20.0),  
            "potencia": 500.0 + (urandom.getrandbits(8) / 255.0 * 2000.0),
            "caudal": 5.0 + (urandom.getrandbits(8) / 255.0 * 10.0)
        }

# ==========================================
# FUNCIONES DE TUYA 
# ==========================================
def calcular_sha256(texto):
    h = uhashlib.sha256(texto.encode('utf-8'))
    return ubinascii.hexlify(h.digest()).decode('utf-8').lower()

def hmac_sha256(key, msg):
    key_bytes, msg_bytes = key.encode('utf-8'), msg.encode('utf-8')
    if len(key_bytes) > 64: key_bytes = uhashlib.sha256(key_bytes).digest()
    if len(key_bytes) < 64: key_bytes += b'\x00' * (64 - len(key_bytes))
    o_key_pad = bytes(b ^ 0x5c for b in key_bytes)
    i_key_pad = bytes(b ^ 0x36 for b in key_bytes)
    inner = uhashlib.sha256(i_key_pad + msg_bytes).digest()
    return ubinascii.hexlify(uhashlib.sha256(o_key_pad + inner).digest()).decode('utf-8').upper()

def generar_firma_tuya(method, url_path, token="", body_str=""):
    # 🌍 CORRECCIÓN BUG UTC+0 CANARIAS: Se elimina la resta de 3600 segundos.
    # El reloj del ESP32 trabajará estrictamente con el Unix Epoch universal.
    hora_utc = time.time()
    
    t = str(int((hora_utc + 946684800) * 1000))
    string_a_firmar = ACCESS_ID + token + t + method + "\n" + calcular_sha256(body_str) + "\n\n" + url_path
    return hmac_sha256(ACCESS_SECRET, string_a_firmar), t

def obtener_token():
    global token_guardado, ultimo_registro_token
    ahora = time.time()
    
    # 💡 Si ya tenemos un token obtenido hace menos de 50 minutos (3000 seg), lo reutilizamos
    if token_guardado and (ahora - ultimo_registro_token < 3000):
        return token_guardado
        
    print("🔑 [Tuya] Solicitando un nuevo Token a la nube...")
    url_path = "/v1.0/token?grant_type=1"
    sign, t = generar_firma_tuya("GET", url_path)
    try:
        res = urequests.get(TUYA_HOST + url_path, headers={"client_id": ACCESS_ID, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"})
        js = res.json()
        res.close()
        
        if js.get("success"):
            token_guardado = js["result"]["access_token"]
            ultimo_registro_token = ahora
            print("✅ [Tuya] Token obtenido y guardado en memoria.")
            return token_guardado
        else:
            print(f"❌ [Tuya] Error de respuesta al pedir Token: {js}")
            return None
    except Exception as e:
        print(f"💥 [Tuya] Error crítico de Red/Memoria al pedir Token: {e}")
        return None

def obtener_estado_dispositivo(token):
    if not token: return None
    url_path = f"/v1.0/devices/{DEVICE_ID}/status"
    sign, t = generar_firma_tuya("GET", url_path, token=token)
    try:
        res = urequests.get(TUYA_HOST + url_path, headers={"client_id": ACCESS_ID, "access_token": token, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"})
        js = res.json()
        res.close()
        
        if js.get("success"):
            return js["result"]
        else:
            print(f"❌ [Tuya] Error de respuesta al pedir Telemetría: {js}")
            # Si el token falló o expiró externamente, forzamos su renovación borrando el guardado
            global token_guardado
            if js.get("code") in [1004, 1010]:
                token_guardado = None
            return None
    except Exception as e:
        print(f"💥 [Tuya] Error crítico de Red/Memoria al pedir Telemetría: {e}")
        return None

# ==========================================
# LÓGICA DE SENSORES SIMULADOS INDIVIDUALES
# ==========================================
def fluctuar(actual, var_max, lim_inf, lim_sup):
    factor = (urandom.getrandbits(8) / 127.5) - 1.0
    return max(lim_inf, min(actual + (factor * var_max), lim_sup))

def simular_clima(id_disp):
    estado = estados_simulacion[id_disp]
    estado["temp_int"] = fluctuar(estado["temp_int"], 0.3, 15.0, 35.0)
    estado["hum_int"] = fluctuar(estado["hum_int"], 1.5, 30.0, 80.0)
    return [
        {"code": "va_temperature", "value": int(estado["temp_int"] * 10)},
        {"code": "va_humidity", "value": int(estado["hum_int"])}
    ]

def simular_clima_exterior(id_disp):
    estado = estados_simulacion[id_disp]
    estado["temp_ext"] = fluctuar(estado["temp_ext"], 0.5, 12.0, 35.0)
    estado["hum_ext"] = fluctuar(estado["hum_ext"], 2.0, 40.0, 90.0)
    return [
        {"code": "va_temperature", "value": int(estado["temp_ext"] * 10)},
        {"code": "va_humidity", "value": int(estado["hum_ext"])}
    ]

def simular_energia(id_disp):
    estado = estados_simulacion[id_disp]
    estado["potencia"] = fluctuar(estado["potencia"], 200.0, 50.0, 5000.0)
    return round(estado["potencia"], 2)

def simular_agua(id_disp):
    estado = estados_simulacion[id_disp]
    estado["caudal"] = fluctuar(estado["caudal"], 3.0, 0.0, 50.0)
    return round(estado["caudal"], 2)

# ==========================================
# BUCLE PRINCIPAL (SIMULADOR DE FLOTA)
# ==========================================
def main():
    print("\n⚡ INICIANDO SIMULADOR DE FLOTA IOT MULTI-INSTALACIÓN ⚡")
    # conectar_wifi() # Descomentar si usas la función de WiFi propia
    
    # 🌍 CORRECCIÓN BUG UTC+0 CANARIAS: Sincronización NTP OBLIGATORIA
    print("⏳ Sincronizando reloj atómico por NTP...")
    try:
        ntptime.settime()
        print("✅ Reloj sincronizado con éxito en UTC absoluto.")
    except Exception as e:
        print(f"⚠️ Aviso: No se pudo sincronizar NTP (Revisar conexión a Internet): {e}")

    cliente_mqtt = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS)
    try: cliente_mqtt.connect()
    except: pass
    
    inicializar_estados_simulacion()
    
    while True:
        gc.collect()
        print(f"\n[{time.ticks_ms()}] Recolectando datos de la flota...")
        
        # Intentará leer el token (reutiliza el de memoria o pide uno nuevo si pasaron 50 min)
        token = obtener_token()
        timestamp_actual = int(time.time() + 946684800)
        
        # PROCESAMOS CADA SENSOR DEL INVENTARIO UNO POR UNO
        for sensor in INVENTARIO_SENSORES:
            id_disp = sensor["id_dispositivo"]
            
            # Plantilla base del paquete
            payload = {
                "metadatos": sensor, 
                "timestamp_local": timestamp_actual
            }
            
            # Rellenamos solo los datos que correspondan a su tipo
            if sensor["tipo_sensor"] == "hibrido_tuya":
                datos_tuya = obtener_estado_dispositivo(token) if token else None
                if datos_tuya:
                    payload["sensor_clima_interior"] = datos_tuya
            
            elif sensor["tipo_sensor"] == "clima_interior":
                payload["sensor_clima_interior"] = simular_clima(id_disp)
                
            elif sensor["tipo_sensor"] == "clima_exterior": 
                payload["sensor_clima_exterior"] = simular_clima_exterior(id_disp)
                
            elif sensor["tipo_sensor"] == "energia":
                payload["consumo_electrico_W"] = simular_energia(id_disp)
                
            elif sensor["tipo_sensor"] == "agua":
                payload["caudal_agua_Lmin"] = simular_agua(id_disp)

            # Publicamos en su tópico específico jerárquico
            topic = "canarias/{}/{}/{}/{}/{}/datos".format(
                sensor["tipo_instalacion"], sensor["isla"], sensor["municipio"], sensor["centro"], id_disp
            ).encode('utf-8')
            
            mensaje_json = json.dumps(payload)
            try:
                cliente_mqtt.publish(topic, mensaje_json)
                print(f"✅ Enviado: [{id_disp}] -> {topic.decode('utf-8')}")
            except Exception as e:
                print(f"⚠️ Error MQTT con {id_disp}: {e}")
            
            time.sleep(0.5) 
            
        print(f"⏳ Flota procesada. Esperando {INTERVALO_PRUEBAS_SEG} seg...")
        time.sleep(INTERVALO_PRUEBAS_SEG)

if __name__ == "__main__":
    main()

