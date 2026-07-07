import network
import time
import ubinascii
import uhashlib
import urequests
import json
import machine
import gc
import urandom
import ntptime 
from umqtt.simple import MQTTClient

# ==========================================================================
# --- CONFIGURACIONES DE ENTORNO (CREDENCIALES OCULTAS PARA SEGURIDAD) ---
# ==========================================================================
WIFI_SSID = "TU_SSID_WIFI"
WIFI_PASS = "TU_PASSWORD_WIFI"
MQTT_SERVER = "192.168.1.130"
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
CLIENT_ID = ubinascii.hexlify(machine.unique_id())
INTERVALO_CICLO_SEG = 30  # Frecuencia de actualización física del gemelo

# Credenciales de tu sensor Tuya Físico (Rotadas y Ocultas)
ACCESS_ID = "[CREDENCIAL_OCULTA_ACCESS_ID]"
ACCESS_SECRET = "[CREDENCIAL_OCULTA_ACCESS_SECRET]" 
DEVICE_ID = "[CREDENCIAL_OCULTA_DEVICE_ID]"
TUYA_HOST = "https://openapi.tuyaeu.com"

# --- CONFIGURACIÓN DE PINES 4G (LILYGO T-SIM A7670E / 7600G) ---
USAR_4G = True  # Cambia a False para usar Wi-Fi local
MODEM_TX = 26
MODEM_RX = 27
MODEM_PWR = 4
APN_OPERADORA = "lowi.private.omv.es" # Ajusta a tu operadora (ej. orangeworld)

# Variables de caché para Token
token_guardado = None
ultimo_registro_token = 0

# --- SISTEMA STORE AND FORWARD (BÚFER LOCAL) ---
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
    # --- CORRECCIÓN DE FRONTERA HORARIA DE CANARIAS (UTC+0 EN INVIERNO) ---
    # El epoch nativo de time.time() ya opera en base UTC. 

  
    hora_utc = time.time() 
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
    except Exception as e:
        print(f"⚠️ Fallo al obtener token: {e}")
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
# 📡 INTERFACES DE RED HÍBRIDA (WI-FI / PROTOCOLOS 4G ACTUALIZADOS)
# ==========================================================================

def conectar_wifi():
    print("📡 Iniciando conexión a red local Wi-Fi...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASS)
    timeout = 0
    while not wlan.isconnected() and timeout < 15:
        time.sleep(1)
        timeout += 1
        
    if wlan.isconnected():
        print("✅ Conexión Wi-Fi establecida.")
        # --- NUEVO: Sincronización del reloj ---
        try:
            print("⏳ Sincronizando reloj interno vía NTP...")
            ntptime.settime()  # Fija la hora UTC actual en el ESP32
            print("✅ Reloj sincronizado con éxito.")
        except Exception as e:
            print(f"⚠️ Error al sincronizar NTP: {e}")
        # ----------------------------------------
    else:
        print("❌ Error: No se pudo conectar al Wi-Fi.")
    return wlan

def inicializar_modem_hardware():
    print("🚀 [Hardware] Inicializando canal celular de grado industrial...")
    
    # 1. Secuencia estricta de encendido (Tiempos críticos SIMCom)
    pwr_key = machine.Pin(MODEM_PWR, machine.Pin.OUT)
    pwr_key.value(1)
    time.sleep_ms(2500) 
    pwr_key.value(0)
    
    print("⏳ Esperando estabilidad del firmware del módem (8 segundos)...")
    time.sleep(8) 
    
    uart = machine.UART(1, baudrate=115200, tx=MODEM_TX, rx=MODEM_RX, timeout=5000)
    if uart.any(): uart.read()
    
    # 2. Configuración base del transceptor
    comandos_base = [
        ("AT\r\n", "OK"),
        # ("AT+CPIN=\"pin\"\r\n", "READY"), # Descomentar y poner PIN si la SIM lo requiere
        ("AT+CFUN=1\r\n", "OK"),                        
        (f'AT+CGDCONT=1,"IP","{APN_OPERADORA}"\r\n', "OK")                          
    ]
    
    for cmd, resp in comandos_base:
        print(f"➡️ Enviando: {cmd.strip()}")
        uart.write(cmd)
        time.sleep(2.5) 
        if uart.any():
            print(f"📥 Respuesta: {uart.read().decode('utf-8', 'ignore').strip()}")
            
    print("⏳ Esperando registro en la red celular...")
    time.sleep(5)
    
    uart.write("AT+CREG?\r\n")
    time.sleep(1)
    if uart.any(): print(f"📥 Estado de red: {uart.read().decode('utf-8', 'ignore').strip()}")

    # 3. Llamada de datos y limpieza de buffers antes de entregar UART a MicroPython
    print("📞 Marcando número de conexión de datos (ATD*99#)...")
    uart.write("ATD*99#\r\n")
    time.sleep(2)
    if uart.any(): print(f"📥 Respuesta marcación: {uart.read().decode('utf-8', 'ignore').strip()}")
    
    return uart

def conectar_4g():
    uart = inicializar_modem_hardware()
    
    print("📶 [Celular] Levantando túnel PPP en MicroPython...")
    ppp = network.PPP(uart)
    ppp.active(True)
    ppp.connect(authmode=ppp.AUTH_NONE, username="", password="")
    
    intentos = 0
    while not ppp.isconnected() and intentos < 15:
        print(f"⏳ PPP negociando conexión IP con {APN_OPERADORA}...")
        time.sleep(2)
        intentos += 1
        
    if ppp.isconnected():
        print("✅ [OUT-OF-BAND] Conexión 4G establecida exitosamente.")
        print("Configuración IP (Celular):", ppp.ifconfig())
        
        # --- PARCHE MAESTRO: INYECCIÓN DNS ---
        # MicroPython olvida el DNS al levantar PPP. Usamos una antena virtual
        # desconectada para inyectar el DNS de Google globalmente.
        print("💉 Inyectando DNS global para habilitar peticiones a Tuya Cloud...")
        sta = network.WLAN(network.STA_IF)
        sta.active(True)
        sta.disconnect()
        sta.ifconfig(('10.254.254.254', '255.255.255.0', '10.254.254.1', '8.8.8.8'))
        
        return ppp
    else:
        print("❌ Error: Tiempo de espera PPP agotado.")
        return None

# ==========================================================================
# 🚀 CICLO DE EJECUCIÓN CIENTÍFICO
# ==========================================================================

def main():
    print("🤖 INICIANDO ENTORNO DE GEMELO DIGITAL CON BASE FÍSICA COHERENTE")
    
    # ---------------- LÓGICA DE CONMUTACIÓN DE TRANSPORTE ----------------
    # Apagamos Wi-Fi por seguridad si vamos a usar 4G puro
    if USAR_4G:
        print("Modo de transporte: RED CELULAR 4G LTE")
        network.WLAN(network.AP_IF).active(False)
        interfaz_red = conectar_4g()
    else:
        print("Modo de transporte: RED LOCAL WI-FI")
        interfaz_red = conectar_wifi()
    # ---------------------------------------------------------------------

    cliente_mqtt = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS, keepalive=60)
    try: 
        cliente_mqtt.connect()
        print("✅ Conectado al Broker MQTT.")
    except: 
        cliente_mqtt = None
        print("⚠️ Broker MQTT inalcanzable. Iniciando en modo offline (Store-and-Forward).")

    temp_ultimas = {"col_clima_int_01": 21.0, "hosp_clima_int_01": 22.0}

    while True:
        gc.collect()

        # ====================================================================
        # 🛡️ WATCHDOG DE RED HÍBRIDO (VIGILA Y RESTAURA CONEXIONES)
        # ====================================================================
        red_activa = False
        
        if interfaz_red and interfaz_red.isconnected():
            red_activa = True
        else:
            print("⚠️ [Red] Conexión física perdida. Intentando restaurar...")
            if USAR_4G:
                interfaz_red = conectar_4g()
            else:
                interfaz_red = conectar_wifi()
            
            if interfaz_red and interfaz_red.isconnected():
                red_activa = True
                cliente_mqtt = None 

        if red_activa and cliente_mqtt is None:
            try:
                print("🔄 [MQTT] Reconectando al broker MQTT...")
                cliente_mqtt = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS, keepalive=60)
                cliente_mqtt.connect()
                print("✅ [MQTT] Conexión establecida. Se enviará el búfer atrasado.")
            except Exception as e:
                print("❌ [MQTT] Error de reconexión:", e)
                cliente_mqtt = None
        # ====================================================================

        token = obtener_token_tuya()
        t_real_ancla = obtener_temperatura_real(token)
        
        if t_real_ancla is None:
            t_real_ancla = 22.4 
            print("⚠️ Conexión Tuya caída. Usando ancla térmica histórica de respaldo.")

        hora_solar = (int(time.time() /3600 ) % 24)
        timestamp_epoch = int(time.time() + 946684800)

        print(f"\n🌍 [Sincronización Sensor Real] Baseline Térmico Tuya: {t_real_ancla} °C")

        # --- ENVÍO DEL SENSOR REAL ---
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
                print(f"✅ [Sensor Real Tuya] Dato original enviado ({t_real_ancla} °C)")
        except:
            pass

        # --- ITERACIÓN DE LA FLOTA Y SIMULACIÓN FÍSICA ---
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
            
            # --- VACIADO DEL BÚFER SI HAY CONEXIÓN ---
            if cliente_mqtt:
                try:
                    while len(buffer_offline) > 0:
                        msg_perdido = buffer_offline[0]
                        cliente_mqtt.publish(msg_perdido['topic'], msg_perdido['payload'])
                        buffer_offline.pop(0)  
                        print("✅ [Recuperado] Trama enviada desde el búfer local.")
                        time.sleep(0.1) 
                except:
                    cliente_mqtt = None 

            # --- ENVÍO DE LA TRAMA ACTUAL ---
            try:
                if cliente_mqtt:
                    cliente_mqtt.publish(topic, mensaje_json.encode('utf-8'))
                    print(f"✅ [Gemelo Físico] {id_disp} publicado ({tipo_falla})")
                else: 
                    raise Exception("Broker desconectado")
            except Exception as e:
                print(f"⚠️ [Fallo de Red] Guardando {id_disp} en búfer (Store-and-Forward)...")
                cliente_mqtt = None 

                if len(buffer_offline) >= MAX_BUFFER_SIZE:
                    print("🚨 [Búfer Lleno] Descartando la trama más antigua (FIFO).")
                    buffer_offline.pop(0) 
                buffer_offline.append({'topic': topic, 'payload': mensaje_json.encode('utf-8')})

            time.sleep(0.2)  

        time.sleep(INTERVALO_CICLO_SEG)

if __name__ == "__main__":
    main()


