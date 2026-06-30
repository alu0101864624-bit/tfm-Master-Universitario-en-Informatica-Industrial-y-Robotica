import network
import time
import ubinascii
import uhashlib
import urequests
import json
import machine
import ntptime
import gc
from umqtt.simple import MQTTClient

# --- 1. PARAMETRIZACIÓN DE PRUEBAS (SIN DEEP SLEEP) ---
INTERVALO_PRUEBAS_SEG = 30  # Lee y envía datos cada 30 segundos

WIFI_SSID = "JUA5y678658S 4068"
WIFI_PASS = "1yuu7u778"

# --- 2. CONFIGURACIÓN MQTT Y METADATOS DE PROCEDENCIA ---
MQTT_SERVER = "192.168.137.1"
MQTT_PORT = 1883
MQTT_TOPIC = b"canarias/infraestructuras/datos"
ORIGEN_INFRAESTRUCTURA = "Colegio_San_Cristobal_La_Laguna_01"

CLIENT_ID = ubinascii.hexlify(machine.unique_id())

# --- 3. CREDENCIALES Y DISPOSITIVOS TUYA CLOUD ---
ACCESS_ID = "qcg6546746475878uh35r"
ACCESS_SECRET = "be3678890986989c767788987804a3"
TUYA_HOST = "https://openapi.tuyaeu.com"

# ID unívoco del sensor interior real
DEVICE_ID_1 = "bfc286789906898votz"
# ID del futuro sensor exterior (inhabilitado en esta prueba)
DEVICE_ID_2 = "bfc28de767787f8xxxx" 

# ==========================================
# RUTINAS DE CIFRADO Y CONEXIÓN
# ==========================================
def calcular_sha256(texto):
    h = uhashlib.sha256(texto.encode('utf-8'))
    return ubinascii.hexlify(h.digest()).decode('utf-8').lower()

def hmac_sha256(key, msg):
    key_bytes = key.encode('utf-8')
    msg_bytes = msg.encode('utf-8')
    if len(key_bytes) > 64: key_bytes = uhashlib.sha256(key_bytes).digest()
    if len(key_bytes) < 64: key_bytes += b'\x00' * (64 - len(key_bytes))
    o_key_pad = bytes(b ^ 0x5c for b in key_bytes)
    i_key_pad = bytes(b ^ 0x36 for b in key_bytes)
    inner = uhashlib.sha256(i_key_pad + msg_bytes).digest()
    return ubinascii.hexlify(uhashlib.sha256(o_key_pad + inner).digest()).decode('utf-8').upper()

def generar_firma_tuya(method, url_path, token="", body_str=""):
    t = str(int((time.time() + 946684800) * 1000))
    content_sha256 = calcular_sha256(body_str)
    url_completa_firma = method + "\n" + content_sha256 + "\n\n" + url_path
    string_a_firmar = ACCESS_ID + token + t + url_completa_firma
    return hmac_sha256(ACCESS_SECRET, string_a_firmar), t

def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)
        inicio = time.time()
        while not wlan.isconnected() and (time.time() - inicio) < 15:
            time.sleep(0.5)
    if wlan.isconnected():
        ntptime.host = "pool.ntp.org"
        try: ntptime.settime()
        except: pass
        return True
    return False

# ==========================================
# LÓGICA DE SENSORES REALES Y SIMULADOS
# ==========================================
def consultar_estado_dispositivo(device_id, token):
    """Consulta la API real de Tuya para un sensor físico"""
    url_path = f"/v1.0/devices/{device_id}/status"
    sign, t = generar_firma_tuya("GET", url_path, token=token)
    gc.collect()
    try:
        res = urequests.get(TUYA_HOST + url_path, headers={
            "client_id": ACCESS_ID, "access_token": token, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"
        })
        datos = res.json().get("result")
        res.close()
        return datos
    except:
        return None

def simular_sensor_tuya_exterior():
    """Emula el formato JSON exacto de un dispositivo Tuya de temperatura/humedad"""
    semilla = time.ticks_ms()
    # Temperatura simulada entre 15.0 y 25.0 ºC (Tuya lo manda multiplicado por 10)
    temp_ext_sim = int(150 + (semilla % 100))
    # Humedad exterior simulada entre 40% y 80%
    hum_ext_sim = int(40 + (semilla % 40))
    
    return [
        {"code": "va_temperature", "value": temp_ext_sim},
        {"code": "va_humidity", "value": hum_ext_sim},
        {"code": "battery_percentage", "value": 100}
    ]

def simular_sensores_faltantes():
    """Simula el Vatímetro y Caudalímetro industrial"""
    semilla = time.ticks_ms()
    potencia_simulada = round(1500.0 + (semilla % 2000), 2)
    caudal_simulado = round((semilla % 450) / 10.0, 2)
    return {
        "potencia_activa_W": potencia_simulada,
        "caudal_agua_Lmin": caudal_simulado
    }

# ==========================================
# ORQUESTACIÓN Y EMPAQUETADO
# ==========================================
def obtener_datos_completos():
    url_path_token = "/v1.0/token?grant_type=1"
    sign, t = generar_firma_tuya("GET", url_path_token)
    gc.collect()
    
    try:
        res = urequests.get(TUYA_HOST + url_path_token, headers={
            "client_id": ACCESS_ID, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"
        })
        token = res.json().get("result", {}).get("access_token")
        res.close()
        
        if not token: 
            return None
        
        # 1. Sensor físico real (Interior)
        print("-> Consultando Sensor Tuya REAL de Aula Principal...")
        datos_sensor_1 = consultar_estado_dispositivo(DEVICE_ID_1, token)
        
        # 2. Sensor Exterior (Opción real inhabilitada, usando simulación)
        print("-> Simulando Sensor Tuya de Fachada Exterior...")
        # datos_sensor_2 = consultar_estado_dispositivo(DEVICE_ID_2, token) # <-- DESCOMENTAR PARA USAR SENSOR FÍSICO
        datos_sensor_2 = simular_sensor_tuya_exterior()                     # <-- COMENTAR CUANDO TENGAS EL SENSOR FÍSICO
        
        # 3. Simulación de Vatímetro y Caudalímetro
        print("-> Generando telemetría simulada para energía y agua...")
        datos_simulados = simular_sensores_faltantes()
        
        # Empaquetado de todo en un único JSON macro
        payload_empaquetado = {
            "procedencia": ORIGEN_INFRAESTRUCTURA,
            "timestamp_local": int(time.time() + 946684800),
            "sensor_clima_interior": datos_sensor_1,
            "sensor_clima_exterior": datos_sensor_2,
            "vatimetro_simulado": datos_simulados
        }
        return payload_empaquetado
    except Exception as e:
        print("Error en el proceso de recolección:", e)
        return None

# ==========================================
# BUCLE INFINITO (MODO LABORATORIO)
# ==========================================
def main():
    print("\n⚡ INICIANDO ESP32 EN MODO LABORATORIO (Bucle Continuo) ⚡")
    if conectar_wifi():
        print("Wi-Fi Conectado. IP local:", network.WLAN(network.STA_IF).ifconfig()[0])
    
    cliente = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT)
    
    while True:
        if not network.WLAN(network.STA_IF).isconnected():
            conectar_wifi()
            
        print(f"\n[{time.ticks_ms()}] Iniciando recolección de datos...")
        paquete = obtener_datos_completos()
        
        if paquete:
            mensaje_json = json.dumps(paquete)
            print("📦 Payload generado:", mensaje_json)
            try:
                cliente.connect()
                cliente.publish(MQTT_TOPIC, mensaje_json.encode('utf-8'))
                cliente.disconnect()
                print("✅ Paquete enviado a Node-RED con éxito.")
            except Exception as e:
                print("❌ Fallo en el envío MQTT:", e)
        
        print(f"⏳ Esperando {INTERVALO_PRUEBAS_SEG} segundos para la siguiente iteración...")
        time.sleep(INTERVALO_PRUEBAS_SEG)

if __name__ == "__main__":
    main()