import network
import time
import ubinascii
import uhashlib
import json
import machine
import gc
from machine import UART, Pin
from umqtt.simple import MQTTClient

# --- CONFIGURACIONES DE ENTORNO EN FRONTERA ---
AP_SSID = "GAT"
AP_PASS = "Canaria26"
MQTT_SERVER = "192.168.4.2" 
MQTT_PORT = 1883
CLIENT_ID = ubinascii.hexlify(machine.unique_id())

# Pines LILYGO T-SIM A7670E
SIM7000_PWRKEY = 4
SIM7000_PIN_RST = 5
SIM7000_TX = 26
SIM7000_RX = 27

# Credenciales Tuya
ACCESS_ID = "qcgu49q8rtgaaesuh35r"
ACCESS_SECRET = "be3a6bba002040f2bc09ac7628c404a3" 
DEVICE_ID = "bfc28dedd9d1d787f8votz"
TUYA_HOST = "openapi.tuyaeu.com"

token_guardado = None
ultimo_registro_token = 0

# Inicialización del reloj estimada directo en el año 2026 (Evita desfases iniciales)
tuya_base_time_ms = 1782902150000 
local_sync_time_s = time.time()

# Configuración de la UART con búfer ampliado para el Módem
uart = UART(1, baudrate=115200, tx=SIM7000_TX, rx=SIM7000_RX, rxbuf=2048, timeout=8000)

# ==========================================================================
# --- CLIENTE HTTP/HTTPS NATIVO CELULAR (CON FILTRO LF CONTINUO) ---
# ==========================================================================

def enviar_at_comando(cmd, respuesta_esperada, timeout_seg=5):
    if uart.any(): uart.read() 
    uart.write(cmd + "\r\n")
    fin = time.time() + timeout_seg
    buffer = ""
    while time.time() < fin:
        if uart.any():
            buffer += uart.read().decode('utf-8', 'ignore')
            if respuesta_esperada in buffer:
                return buffer
        time.sleep_ms(50)
    return buffer

def tuya_get_hardware_ssl(url_path):
    global token_guardado
    print(f"📡 [Hardware SSL] Solicitando vía módem a https://{TUYA_HOST}{url_path}")
    
    token = token_guardado if token_guardado else ""
    sign, t = generar_firma_tuya("GET", url_path, token=token)
    
    # Asegurar cierre de sesiones huérfanas previas y activar contexto celular
    enviar_at_comando("AT+HTTPTERM", "OK", timeout_seg=2)
    enviar_at_comando("AT+CGACT=1,1", "OK", timeout_seg=5)
    
    # 1. Configurar e iniciar motor HTTP nativo
    enviar_at_comando("AT+HTTPINIT", "OK")
    enviar_at_comando('AT+HTTPPARA="CID",1', "OK")
    enviar_at_comando(f'AT+HTTPPARA="URL","https://{TUYA_HOST}{url_path}"', "OK")
    
    # 2. Inyección de cabeceras seguras usando '\n' para evitar truncar el comando AT
    cabeceras_str = f"client_id: {ACCESS_ID}\nsign: {sign}\nt: {t}\nsign_method: HMAC-SHA256"
    if token:
        cabeceras_str += f"\naccess_token: {token}"
        
    enviar_at_comando(f'AT+HTTPPARA="USERDATA","{cabeceras_str}"', "OK")
    
    # 3. Lanzar petición y parsear la longitud de la respuesta devuelta por la nube
    print("⏳ Esperando respuesta TLS de Tuya Cloud...")
    res = enviar_at_comando("AT+HTTPACTION=0", "+HTTPACTION:", timeout_seg=15)
    
    json_resultado = None
    if "+HTTPACTION: 0,200" in res:
        bytes_a_leer = "150"
        try:
            for linea in res.split("\n"):
                if "+HTTPACTION:" in linea:
                    bytes_a_leer = linea.strip().split(",")[-1]
        except:
            pass
            
        print(f"📥 [Hardware] Extrayendo {bytes_a_leer} bytes de la respuesta de Tuya...")
        uart.write(f"AT+HTTPREAD=0,{bytes_a_leer}\r\n")
        time.sleep(1.5)
        
        if uart.any():
            datos_crudos = uart.read().decode('utf-8', 'ignore')
            
            if "{" in datos_crudos:
                inicio_json = datos_crudos.find("{")
                fin_json = datos_crudos.rfind("}") + 1
                try:
                    json_resultado = json.loads(datos_crudos[inicio_json:fin_json])
                except Exception as e:
                    print("⚠️ Error al parsear JSON devuelto por hardware:", e)
    else:
        print(f"❌ El módem no recibió un HTTP 200 válido. Respuesta: {res.strip()}")
        
    enviar_at_comando("AT+HTTPTERM", "OK")
    return json_resultado

# ==========================================================================
# --- MOTOR CRIPTOGRÁFICO SINTONIZADO ---
# ==========================================================================
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
    global tuya_base_time_ms, local_sync_time_s
    
    # Cálculo preciso del tiempo transcurrido desde el arranque o sincronización
    delta_s = time.time() - local_sync_time_s
    t_ms = tuya_base_time_ms + (delta_s * 1000)
        
    t = str(int(t_ms))
    string_a_firmar = ACCESS_ID + token + t + method + "\n" + calcular_sha256(body_str) + "\n\n" + url_path
    return hmac_sha256(ACCESS_SECRET, string_a_firmar), t

def obtener_token():
    global token_guardado, ultimo_registro_token, tuya_base_time_ms, local_sync_time_s
    ahora = time.time()
    if token_guardado and (ahora - ultimo_registro_token < 3000): return token_guardado
        
    url_path = "/v1.0/token?grant_type=1"
    
    for intento in range(3):
        gc.collect() 
        print(f"🌐 [Tuya] Solicitando Token mediante canal HTTP (Intento {intento+1}/3)...")
        js = tuya_get_hardware_ssl(url_path)
        if js and js.get("success"):
            token_guardado = js["result"]["access_token"]
            ultimo_registro_token = ahora
            print("✅ [Tuya] ¡Token criptográfico obtenido con éxito!")
            return token_guardado
        elif js:
            print(f"❌ [Tuya] Nube respondió: {js}")
            if js.get("code") == 1013 and "t" in js:
                # Si el servidor aún pide ajuste fino de milisegundos, lo asimilamos aquí
                tuya_base_time_ms = js["t"]
                local_sync_time_s = time.time()
                print(f"⏰ [Reloj] Resincronización fina aplicada. Reintentando...")
        else:
            print("⚠️ Reintentando lectura por inconsistencia en respuesta...")
        time.sleep(2)
    return None

def obtener_estado_tuya(token):
    global tuya_base_time_ms, local_sync_time_s
    if not token: return None
    url_path = f"/v1.0/devices/{DEVICE_ID}/status"
    
    for intento in range(3):
        gc.collect() 
        print(f"📡 [Tuya] Descargando estado del sensor (Intento {intento+1}/3)...")
        js = tuya_get_hardware_ssl(url_path)
        if js and js.get("success"):
            print("✅ [Tuya] Telemetría descargada correctamente.")
            return js["result"]
        elif js:
            print(f"❌ [Tuya] Error de telemetría: {js}")
            if js.get("code") == 1013 and "t" in js:
                tuya_base_time_ms = js["t"]
                local_sync_time_s = time.time()
    return None

# ==========================================================================
# --- CONTROL DE INFRAESTRUCTURA MÓDEM ---
# ==========================================================================
def inicializar_modem_a7670e(apn="lowi.private.omv.es", pin_sim="TuPin"):
    print("🚀 [Hardware] Inicializando canal celular LILYGO A7670E...")
    
    rst_pin = Pin(SIM7000_PIN_RST, Pin.OUT)
    rst_pin.value(0) # Apagar y encender físicamente el chip para drenar bloqueos de socket anteriores
    time.sleep_ms(300)
    rst_pin.value(1) 
    time.sleep_ms(100)
    
    pwr_key = Pin(SIM7000_PWRKEY, Pin.OUT)
    pwr_key.value(1)
    time.sleep_ms(2500) 
    pwr_key.value(0)
    
    print("⏳ Esperando estabilidad del firmware A7670E...")
    time.sleep(8) 
    
    comandos_base = [
        ("AT", "OK"),
        (f'AT+CPIN="{pin_sim}"', "READY"),         
        ("AT+CFUN=1", "OK"),                        
        (f'AT+CGDCONT=1,"IP","{apn}"', "OK")                          
    ]
    
    for cmd, resp in comandos_base:
        print(f"➡️ Enviando al A7670E: {cmd}")
        r = enviar_at_comando(cmd, resp, timeout_seg=4)
        print(f"📥 Respuesta:\n{r.strip()}")
            
    print("⏳ Registrando en antena celular...")
    time.sleep(4)
    enviar_at_comando("AT+CREG?", "OK")
    return True

# ==========================================================
# SECUENCIA DE ARRANQUE EN RED
# ==========================================================

print("📶 Preparando canal de comandos AT del módem...")
configuracion_correcta = inicializar_modem_a7670e()

if configuracion_correcta:
    print("🔒 Solicitando credenciales seguras a Tuya Cloud...")
    token = obtener_token()
    
    print("📡 [Wi-Fi AP] Activando Punto de Acceso Local (GAT)...")
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid=AP_SSID, password=AP_PASS, authmode=network.AUTH_WPA2_PSK, channel=6)
    while not ap.active(): pass
    print(f"✅ Zona Wi-Fi activa. SSID: {AP_SSID} | Gateway IP: {ap.ifconfig()[0]}")
    
    print("⏳ Conecta tu ordenador a la red 'GAT'. Buscando Broker MQTT en 15 seg...")
    time.sleep(15)
    
    cliente_mqtt = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT) 
    mqtt_conectado = False
    for i in range(5):
        try:
            cliente_mqtt.connect()
            print("✅ ¡MQTT enlazado correctamente al PC!")
            mqtt_conectado = True
            break
        except Exception as e:
            print(f"⚠️ Buscando al PC en {MQTT_SERVER}... (Intento {i+1}/5)")
            time.sleep(4)

# ==========================================================
# LAZO DE PRODUCCIÓN CONTINUA
# ==========================================================
while configuracion_correcta and mqtt_conectado:
    token = obtener_token()
    datos_reales = obtener_estado_tuya(token)
    
    if datos_reales:
        payload = {
            "id_dispositivo": DEVICE_ID,
            "timestamp_epoch": int(time.time() + 946684800),
            "telemetria": datos_reales
        }
        
        topic = b"canarias/produccion/sensor_fisico/datos"
        mensaje_json = json.dumps(payload)
        
        try:
            cliente_mqtt.publish(topic, mensaje_json.encode('utf-8'))
            print(f"🚀 [Hacia PC vía MQTT] -> {mensaje_json}")
        except Exception as e:
            print("⚠️ Falla de envío MQTT, reintentando...")
            try: cliente_mqtt.connect()
            except: pass
    else:
        print("⚠️ No se obtuvieron datos válidos en este ciclo.")
        
    time.sleep(30)