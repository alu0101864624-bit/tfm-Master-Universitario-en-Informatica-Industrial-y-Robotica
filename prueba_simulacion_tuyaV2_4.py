import network
import time
import ubinascii
import json
import machine
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
INTERVALO_PRUEBAS_SEG = 300  # Tiempo entre barridos de la flota

# ==========================================================================
# 🌟 INVENTARIO MULTI-INSTALACIÓN (Solo: temp_int, temp_ext, potencia, caudal)
# ==========================================================================
INVENTARIO_SENSORES = [
    # --- INSTALACIÓN 1: COLEGIO (Tenerife) ---
    {
        "id_dispositivo": "col_clima_int_01", "tipo_sensor": "temperatura_interior",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Aula_Principal"
    },
    {
        "id_dispositivo": "col_clima_ext_01", "tipo_sensor": "temperatura_exterior",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Patio"
    },
    {
        "id_dispositivo": "col_potencia_01", "tipo_sensor": "potencia",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Cuadro_General"
    },
    {
        "id_dispositivo": "col_caudal_01", "tipo_sensor": "caudal",
        "isla": "Tenerife", "municipio": "La_Laguna", "tipo_instalacion": "Colegio", "centro": "San_Cristobal", "zona": "Entrada_Agua"
    },
    
    # --- INSTALACIÓN 2: HOSPITAL (Gran Canaria) ---
    {
        "id_dispositivo": "hosp_clima_int_01", "tipo_sensor": "temperatura_interior",
        "isla": "Gran_Canaria", "municipio": "Las_Palmas", "tipo_instalacion": "Hospital", "centro": "Insular", "zona": "Urgencias"
    },
    {
        "id_dispositivo": "hosp_potencia_01", "tipo_sensor": "potencia",
        "isla": "Gran_Canaria", "municipio": "Las_Palmas", "tipo_instalacion": "Hospital", "centro": "Insular", "zona": "Quirofano_1"
    },
    
    # --- INSTALACIÓN 3: PLANTA ENERGÍA SOLAR (Lanzarote) ---
    {
        "id_dispositivo": "sol_potencia_01", "tipo_sensor": "potencia",
        "isla": "Lanzarote", "municipio": "San_Bartolome", "tipo_instalacion": "Planta_Solar", "centro": "FV_Timanfaya", "zona": "Inversor_A"
    },
    
    # --- INSTALACIÓN 4: DEPURADORA EDAR (Fuerteventura) ---
    {
        "id_dispositivo": "edar_caudal_01", "tipo_sensor": "caudal",
        "isla": "Fuerteventura", "municipio": "Puerto_Rosario", "tipo_instalacion": "Depuradora", "centro": "EDAR_Norte", "zona": "Bombeo_Entrada"
    }
]

# Almacén dinámico para retener el último estado de cada sensor y poder repetir valores
estados_simulacion = {}

def inicializar_estados():
    """Genera valores base lógicos para cada dispositivo de la flota"""
    for sensor in INVENTARIO_SENSORES:
        estados_simulacion[sensor["id_dispositivo"]] = {
            "temp_int": 21.0 + (urandom.getrandbits(8) / 255.0 * 3.0),
            "temp_ext": 18.0 + (urandom.getrandbits(8) / 255.0 * 8.0),
            "potencia": 1000.0 + (urandom.getrandbits(8) / 255.0 * 4000.0),
            "caudal": 5.0 + (urandom.getrandbits(8) / 255.0 * 20.0)
        }

def fluctuar_o_repetir(actual, var_max, lim_inf, lim_sup):
    """
    Simula el comportamiento físico:
    - 25% de probabilidad de mantener el dato idéntico (repetirse).
    - 75% de probabilidad de fluctuar de forma suave.
    """
    probabilidad = urandom.getrandbits(8) % 100
    
    if probabilidad < 25:
        return actual  # Repite el valor exacto del ciclo anterior
    else:
        # Genera un factor entre -1.0 y 1.0
        factor = (urandom.getrandbits(8) / 127.5) - 1.0
        nuevo_valor = actual + (factor * var_max)
        return max(lim_inf, min(nuevo_valor, lim_sup))

# ==========================================================================
# 🤖 MOTOR DE SIMULACIÓN (CON PÉRDIDAS DE DATOS / NULLS)
# ==========================================================================
def obtener_lecturas_simuladas(sensor):
    id_disp = sensor["id_dispositivo"]
    tipo = sensor["tipo_sensor"]
    estado = estados_simulacion[id_disp]
    
    # 💥 SIMULACIÓN DE DATOS FALTANTES: 12% de probabilidad de fallo de lectura (envía None)
    if (urandom.getrandbits(8) % 100) < 12:
        print(f"⚠️  [Fallo Sensor] {id_disp} no respondió (Dato faltante).")
        return None  
        
    if tipo == "temperatura_interior":
        estado["temp_int"] = fluctuar_o_repetir(estado["temp_int"], 0.2, 16.0, 27.0)
        return {"temperatura_int_C": round(estado["temp_int"], 1)}
        
    elif tipo == "temperatura_exterior":
        estado["temp_ext"] = fluctuar_o_repetir(estado["temp_ext"], 0.5, 10.0, 37.0)
        return {"temperatura_ext_C": round(estado["temp_ext"], 1)}
        
    elif tipo == "potencia":
        estado["potencia"] = fluctuar_o_repetir(estado["potencia"], 300.0, 50.0, 15000.0)
        return {"potencia_W": round(estado["potencia"], 1)}
        
    elif tipo == "caudal":
        estado["caudal"] = fluctuar_o_repetir(estado["caudal"], 1.8, 0.0, 50.0)
        return {"caudal_Lmin": round(estado["caudal"], 2)}
        
    return None

# ==========================================================================
# 🚀 BUCLE PRINCIPAL MULTI-INSTALACIÓN
# ==========================================================================
def main():
    print("\n⚡ INICIANDO SIMULADOR MULTI-INSTALACIÓN (COMPORTAMIENTO REALISTA) ⚡")
    
    # Conexión WiFi básica incorporada
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("🌐 Conectando a la red WiFi...")
        wlan.connect(WIFI_SSID, WIFI_PASS)
        timeout = 0
        while not wlan.isconnected() and timeout < 12:
            time.sleep(1)
            timeout += 1
            
    if wlan.isconnected():
        print("🌐 WiFi Conectado con éxito. IP:", wlan.ifconfig()[0])
    
    # Inicializar Cliente MQTT
    cliente_mqtt = MQTTClient(CLIENT_ID, MQTT_SERVER, port=MQTT_PORT, user=MQTT_USER, password=MQTT_PASS)
    try: 
        cliente_mqtt.connect()
        print("📡 Conectado al Broker MQTT local con éxito.")
    except Exception as e: 
        print(f"⚠️  No se pudo establecer conexión MQTT ({e}). Modo Consola Thonny activado.")
        cliente_mqtt = None
    
    inicializar_estados()
    
    while True:
        gc.collect()
        timestamp_actual = int(time.time() + 946684800)
        print(f"\n🔄 --- NUEVO BARRIDO DE LA FLOTA CANARIA ---")
        
        for sensor in INVENTARIO_SENSORES:
            id_disp = sensor["id_dispositivo"]
            datos_sensores = obtener_lecturas_simuladas(sensor)
            
            # Construcción estructurada del mensaje
            payload = {
                "metadatos": sensor,
                "timestamp_local": timestamp_actual,
                "datos_sensores": datos_sensores  # Puede ser un diccionario válido o 'null'
            }
            
            # Tópico dinámico limpio según la procedencia de cada instalación:
            # canarias/Tipo_Instalacion/Isla/Municipio/Centro/Zona/id_dispositivo/datos
            topic = "canarias/{}/{}/{}/{}/{}/{}/datos".format(
                sensor["tipo_instalacion"], 
                sensor["isla"], 
                sensor["municipio"], 
                sensor["centro"], 
                sensor["zona"], 
                id_disp
            ).encode('utf-8')
            
            mensaje_json = json.dumps(payload)
            
            try:
                if cliente_mqtt:
                    cliente_mqtt.publish(topic, mensaje_json.encode('utf-8'))
                    print(f"✅ [{sensor['tipo_instalacion']}] Enviado: {id_disp} -> {topic.decode('utf-8')}")
                else:
                    raise Exception()
            except:
                # Respaldo si falla el Broker o estás probando sin red
                print(f"🔬 [Simulación Local] {id_disp} -> {mensaje_json}")
            
            time.sleep(0.15) # Retardo corto para suavizar ráfagas en el microcontrolador
            
        print(f"⏳ Barrido de flota completado. Esperando {INTERVALO_PRUEBAS_SEG} segundos...")
        time.sleep(INTERVALO_PRUEBAS_SEG)

if __name__ == "__main__":
    main()