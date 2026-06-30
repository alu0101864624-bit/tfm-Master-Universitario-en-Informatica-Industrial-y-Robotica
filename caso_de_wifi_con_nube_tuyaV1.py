import network
import time
import ubinascii
import uhashlib
import urequests
import json
import machine
import ntptime
from umqtt.simple import MQTTClient

# --- 1. CONFIGURACIÓN DE RED LOCAL (Red A) ---
WIFI_SSID = "JUANffrggr 4068"
WIFI_PASS = "123rgrgt678"

# --- 2. CONFIGURACIÓN DEL SERVIDOR MQTT REMOTO (Red B) ---
MQTT_SERVER = "192.168.137.1"
MQTT_PORT = 1883
MQTT_USER = ""
MQTT_PASS = ""
MQTT_TOPIC = b"casa1/sensor_ambiente"
CLIENT_ID = ubinascii.hexlify(machine.unique_id())

# --- 3. CREDENCIALES TUYA CLOUD ---
ACCESS_ID = "qcgu4tgthytgaaesuh35r"
# Aseguramos que el secret se procese correctamente como texto plano para la firma
ACCESS_SECRET = "be3a6bbargrgtytrr9ac7628c404a3" 
DEVICE_ID = "bfcttyuiuytui8votz"

# Endpoint oficial para el Data Center de Europa Central (visto en tu imagen)
TUYA_HOST = "https://openapi.tuyaeu.com"

# ==========================================
# CÁLCULO ESTRICTO DE LA FIRMA TUYA v2.0
# ==========================================
def calcular_sha256(texto):
    """Calcula el SHA256 Hash de un texto (para el cuerpo de la petición)"""
    h = uhashlib.sha256(texto.encode('utf-8'))
    return ubinascii.hexlify(h.digest()).decode('utf-8').lower()

def hmac_sha256(key, msg):
    """Calcula el HMAC-SHA256 real necesario para firmar en Tuya"""
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
    """
    Construye la firma siguiendo el estándar estricto de Tuya v2.0 Cloud:
    Sign = HMAC_SHA256(client_id + token + t + nonce + string_to_sign, secret)
    """
    t = obtener_tuya_timestamp()
    
    # 1. SHA256 del contenido del Body (vacío para peticiones GET)
    content_sha256 = calcular_sha256(body_str)
    
    # 2. String de la cabecera (Tuya espera las cabeceras vacías o mapeadas en firmas simples)
    headers_str = ""
    
    # 3. Recomponer la URL formal de la firma
    url_completa_firma = method + "\n" + content_sha256 + "\n" + headers_str + "\n" + url_path
    
    # 4. Concatenar los factores según la documentación oficial de Tuya
    string_a_firmar = ACCESS_ID + token + t + url_completa_firma
    
    # 5. Aplicar la firma criptográfica final
    sign = hmac_sha256(ACCESS_SECRET, string_a_firmar)
    
    return sign, t

# ==========================================
# FUNCIONES DE RED Y TIEMPO
# ==========================================
def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("Conectando a la red local:", WIFI_SSID)
        wlan.connect(WIFI_SSID, WIFI_PASS)
        while not wlan.isconnected():
            time.sleep(0.5)
            print(".", end="")
    print("\nWiFi Conectado. IP local:", wlan.ifconfig()[0])
    sincronizar_hora()

def sincronizar_hora():
    print("Sincronizando hora con servidor NTP (pool.ntp.org)...")
    ntptime.host = "pool.ntp.org"
    for i in range(5):
        try:
            ntptime.settime()
            print("¡Reloj interno del ESP32 sincronizado con éxito!")
            return
        except:
            time.sleep(2)
    print("⚠️ No se pudo sincronizar la hora.")

def obtener_tuya_timestamp():
    # Ajuste de época MicroPython (2000) a época Unix (1970) requerida por Tuya
    segundos_unix = time.time() + 946684800
    return str(int(segundos_unix * 1000))

def conectar_mqtt():
    print("Conectando al servidor MQTT remoto...")
    try:
        cliente = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS)
        cliente.connect()
        print("¡Conectado a MQTT con éxito!")
        return cliente
    except Exception as e:
        print("Aviso: No se pudo conectar a MQTT local:", e)
        return None

# ==========================================
# PETICIONES HTTP A TUYA CLOUD API v2.0
# ==========================================
def obtener_token():
    """Solicita un Token de acceso válido a la API de Tuya"""
    url_path = "/v1.0/token?grant_type=1"
    
    # Generamos la firma estricta v2 sin token
    sign, t = generar_firma_tuya("GET", url_path, token="")
    
    url = TUYA_HOST + url_path
    headers = {
        "client_id": ACCESS_ID,
        "sign": sign,
        "t": t,
        "sign_method": "HMAC-SHA256"
    }
    
    try:
        print("-> Solicitando Token de acceso a Tuya Cloud...")
        res = urequests.get(url, headers=headers)
        js = res.json()
        res.close()
        if js.get("success"):
            return js["result"]["access_token"]
        else:
            print("❌ Error de autenticación Tuya Cloud:", js.get("msg"))
    except Exception as e:
        print("Error de red al solicitar Token:", e)
    return None

def obtener_estado_dispositivo(token):
    """Obtiene el último estado reportado (DPS) de los sensores"""
    if not token: 
        return None
        
    url_path = f"/v1.0/devices/{DEVICE_ID}/status"
    
    # Generamos la firma estricta v2 incluyendo el Token de sesión obtenido
    sign, t = generar_firma_tuya("GET", url_path, token=token)
    
    url = TUYA_HOST + url_path
    headers = {
        "client_id": ACCESS_ID,
        "access_token": token,
        "sign": sign,
        "t": t,
        "sign_method": "HMAC-SHA256"
    }
    
    try:
        res = urequests.get(url, headers=headers)
        js = res.json()
        res.close()
        if js.get("success"):
            return js["result"]
        else:
            print("❌ Error al leer dispositivo:", js.get("msg"))
    except Exception as e:
        print("Error de red al consultar el estado:", e)
    return None

# ==========================================
# BUCLE PRINCIPAL
# ==========================================
def main():
    conectar_wifi()
    cliente_mqtt = conectar_mqtt()
    
    while True:
        print("\n🌐 Sincronizando con la nube de Tuya...")
        token = obtener_token()
        
        if token:
            datos = obtener_estado_dispositivo(token)
            if datos:
                mensaje_final = json.dumps({"dps": datos})
                print("🎉 ¡ÉXITO! Datos actuales del dispositivo:", mensaje_final)
                
                if cliente_mqtt:
                    try:
                        cliente_mqtt.publish(MQTT_TOPIC, mensaje_final.encode('utf-8'))
                        print("🚀 Datos enviados hacia Node-RED con éxito.")
                    except:
                        print("Conexión MQTT perdida. Reconectando...")
                        cliente_mqtt = conectar_mqtt()
        
        print("Esperando 30 segundos para la siguiente sincronización...")
        time.sleep(30)

if __name__ == "__main__":
    main()