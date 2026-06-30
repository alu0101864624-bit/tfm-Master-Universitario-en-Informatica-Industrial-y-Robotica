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

# --- 2. CONFIGURACIÓN MQTT Y METADATOS (Viene de V2) ---
MQTT_SERVER = "192.168.1.130"
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
MQTT_TOPIC = b"canarias/infraestructuras/datos"
ORIGEN_INFRAESTRUCTURA = "Colegio_San_Cristobal_La_Laguna_01"
CLIENT_ID = ubinascii.hexlify(machine.unique_id())
INTERVALO_PRUEBAS_SEG = 300

# --- 3. CREDENCIALES TUYA CLOUD ---
ACCESS_ID = ""
ACCESS_SECRET = "" 
DEVICE_ID = ""
TUYA_HOST = "https://openapi.tuyaeu.com"

# ==========================================
# CÁLCULO ESTRICTO DE LA FIRMA TUYA v2.0
# ==========================================
def calcular_sha256(texto):
    h = uhashlib.sha256(texto.encode('utf-8'))
    return ubinascii.hexlify(h.digest()).decode('utf-8').lower()

def hmac_sha256(key, msg):
    key_bytes = key.encode('utf-8')
    msg_bytes = msg.encode('utf-8')
    if len(key_bytes) > 64:
        key_bytes = uhashlib.sha256(key_bytes).digest()
    if len(key_bytes) < 64:
        key_bytes += b'\x00' * (64 - len(key_bytes))
    o_key_pad = bytes(b ^ 0x5c for b in key_bytes)
    i_key_pad = bytes(b ^ 0x36 for b in key_bytes)
    inner = uhashlib.sha256(i_key_pad + msg_bytes).digest()
    return ubinascii.hexlify(uhashlib.sha256(o_key_pad + inner).digest()).decode('utf-8').upper()

def generar_firma_tuya(method, url_path, token="", body_str=""):
    t = obtener_tuya_timestamp()
    content_sha256 = calcular_sha256(body_str)
    url_completa_firma = method + "\n" + content_sha256 + "\n\n" + url_path
    string_a_firmar = ACCESS_ID + token + t + url_completa_firma
    sign = hmac_sha256(ACCESS_SECRET, string_a_firmar)
    return sign, t

# ==========================================
# FUNCIONES DE RED (Con IP estática forzada)
# ==========================================
def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    print("Forzando desconexión de redes antiguas...")
    wlan.disconnect()
    time.sleep(1.0)
    
    print("Configurando IP estática en el rango correcto (192.168.1.x)...")
    wlan.ifconfig(('192.168.1.200', '255.255.255.0', '192.168.1.1', '8.8.8.8'))
    time.sleep(0.5)
    
    print("Conectando a la red WiFi:", WIFI_SSID)
    try:
        wlan.connect(WIFI_SSID, WIFI_PASS)
    except OSError as e:
        pass

    intentos = 0
    while not wlan.isconnected() and intentos < 20:
        time.sleep(0.5)
        print(".", end="")
        intentos += 1
        
    print() 
    if wlan.isconnected():
        print("✅ WiFi Conectado con éxito! IP configurada:", wlan.ifconfig()[0])
        sincronizar_hora()
    else:
        print("❌ ERROR: No se pudo conectar a la red MIWIFI.")

def sincronizar_hora():
    print("Sincronizando hora con servidores NTP...")
    servidores_ntp = ["time.google.com", "time.windows.com", "pool.ntp.org"]
    for servidor in servidores_ntp:
        ntptime.host = servidor
        for intento in range(3):
            try:
                ntptime.settime()
                print("✅ Reloj sincronizado con éxito!")
                return
            except:
                time.sleep(1.5)
    print("❌ ERROR: No se pudo sincronizar la hora por internet.")

def obtener_tuya_timestamp():
    segundos_unix = time.time() + 946684800
    return str(int(segundos_unix * 1000))

def conectar_mqtt():
    try:
        cliente = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS)
        cliente.connect()
        print("✅ Conectado a MQTT local en", MQTT_SERVER)
        return cliente
    except Exception as e:
        print("⚠️ Aviso: No se pudo conectar a MQTT:", e)
        return None

# ==========================================
# PETICIONES A TUYA (EL SENSOR REAL)
# ==========================================
def obtener_token():
    url_path = "/v1.0/token?grant_type=1"
    sign, t = generar_firma_tuya("GET", url_path, token="")
    url = TUYA_HOST + url_path
    headers = {"client_id": ACCESS_ID, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"}
    try:
        res = urequests.get(url, headers=headers)
        js = res.json()
        res.close()
        if js.get("success"):
            return js["result"]["access_token"]
    except Exception as e:
        print("Error al solicitar Token:", e)
    return None

def obtener_estado_dispositivo(token):
    if not token: return None
    url_path = f"/v1.0/devices/{DEVICE_ID}/status"
    sign, t = generar_firma_tuya("GET", url_path, token=token)
    url = TUYA_HOST + url_path
    headers = {"client_id": ACCESS_ID, "access_token": token, "sign": sign, "t": t, "sign_method": "HMAC-SHA256"}
    try:
        res = urequests.get(url, headers=headers)
        js = res.json()
        res.close()
        if js.get("success"):
            return js["result"]
    except Exception as e:
        print("Error de red al consultar el estado:", e)
    return None

# ==========================================
# LÓGICA DE SENSORES SIMULADOS (Viene de V2)
# ==========================================
# Memoria de estado para que los cambios sean graduales
estado_simulacion = {
    "temp_int": 21.0,
    "hum_int": 55.0,
    "temp_ext": 18.0,
    "hum_ext": 60.0,
    "potencia": 2500.0,
    "caudal": 10.0
}

def fluctuar_valor(actual, variacion_maxima, limite_inferior, limite_superior):
    """Suma o resta un pequeño margen al valor actual de forma realista"""
    # Genera un factor aleatorio entre -1.0 y 1.0
    factor_ruido = (urandom.getrandbits(8) / 127.5) - 1.0
    nuevo_valor = actual + (factor_ruido * variacion_maxima)
    
    # Fuerzo a que el valor se mantenga dentro de los límites lógicos
    return max(limite_inferior, min(nuevo_valor, limite_superior))

def simular_sensor_tuya_interior():
    global estado_simulacion
    # Sube o baja máximo 0.2 ºC por ciclo
    estado_simulacion["temp_int"] = fluctuar_valor(estado_simulacion["temp_int"], 0.2, 19.0, 25.0)
    estado_simulacion["hum_int"] = fluctuar_valor(estado_simulacion["hum_int"], 1.0, 45.0, 65.0)
    
    return [
        {"code": "va_temperature", "value": int(estado_simulacion["temp_int"] * 10)},
        {"code": "va_humidity", "value": int(estado_simulacion["hum_int"])},
        {"code": "battery_percentage", "value": 98}
    ]

def simular_sensor_tuya_exterior():
    global estado_simulacion
    # El exterior fluctúa un poco más, hasta 0.5 ºC por ciclo
    estado_simulacion["temp_ext"] = fluctuar_valor(estado_simulacion["temp_ext"], 0.5, 12.0, 30.0)
    estado_simulacion["hum_ext"] = fluctuar_valor(estado_simulacion["hum_ext"], 2.0, 40.0, 85.0)
    
    return [
        {"code": "va_temperature", "value": int(estado_simulacion["temp_ext"] * 10)},
        {"code": "va_humidity", "value": int(estado_simulacion["hum_ext"])},
        {"code": "battery_percentage", "value": 100}
    ]

def simular_sensores_faltantes():
    global estado_simulacion
    # El consumo y el agua cambian más rápido, simulando encendido de equipos o grifos
    estado_simulacion["potencia"] = fluctuar_valor(estado_simulacion["potencia"], 150.0, 1000.0, 3500.0)
    estado_simulacion["caudal"] = fluctuar_valor(estado_simulacion["caudal"], 2.5, 0.0, 45.0)
    
    return {
        "potencia_activa_W": round(estado_simulacion["potencia"], 2),
        "caudal_agua_Lmin": round(estado_simulacion["caudal"], 2)
    }
# ==========================================
# BUCLE PRINCIPAL HÍBRIDO
# ==========================================
def main():
    print("\n⚡ INICIANDO ESP32 HÍBRIDO (TUYA REAL + SIMULACIÓN) ⚡")
    conectar_wifi()
    cliente_mqtt = conectar_mqtt()
    
    while True:
        gc.collect()
        print(f"\n[{time.ticks_ms()}] Iniciando recolección de datos...")
        
        # 1. Obtener datos REALES de la nube
        token = obtener_token()
        if token:
            print("-> Descargando Clima Interior (REAL desde Tuya)...")
            datos_reales_interior = obtener_estado_dispositivo(token)
            
            if datos_reales_interior:
                # 2. Generar datos SIMULADOS
                print("-> Generando telemetría simulada para Exterior y Energía...")
                datos_sim_exterior = simular_sensor_tuya_exterior()                     
                datos_sim_energia = simular_sensores_faltantes()
                
               # 3. Empaquetar el macro-JSON
                payload_empaquetado = {
                    "procedencia": ORIGEN_INFRAESTRUCTURA,
                    "timestamp_local": int(time.time() + 946684800),
                    "sensor_clima_interior": datos_reales_interior, 
                    "sensor_clima_exterior": datos_sim_exterior,    
                    "consumo_electrico_W": datos_sim_energia["potencia_activa_W"],  
                    "caudal_agua_Lmin": datos_sim_energia["caudal_agua_Lmin"]       
                }
                
                mensaje_json = json.dumps(payload_empaquetado)
                print("📦 Payload generado:", mensaje_json)
                
                # 4. Enviar a Node-RED
                if cliente_mqtt:
                    try:
                        cliente_mqtt.publish(MQTT_TOPIC, mensaje_json.encode('utf-8'))
                        print("🚀 Paquete enviado a Node-RED con éxito.")
                    except:
                        print("⚠️ Conexión MQTT perdida. Reconectando...")
                        cliente_mqtt = conectar_mqtt()
            else:
                print("❌ No se pudieron obtener los datos reales del sensor.")
        
        print(f"⏳ Esperando {INTERVALO_PRUEBAS_SEG} segundos para la siguiente sincronización...")
        time.sleep(INTERVALO_PRUEBAS_SEG)

if __name__ == "__main__":
    main()
