# 🌍 IoT-Hybrid-Gateway: Arquitectura de Monitorización y Gemelo Digital para Infraestructuras Canarias

![MicroPython](https://img.shields.io/badge/MicroPython-1.19+-blue.svg)
![MQTT](https://img.shields.io/badge/Protocol-MQTT-yellow.svg)
![Hardware](https://img.shields.io/badge/Hardware-LILYGO_T--SIM7600-green.svg)
![Estado](https://img.shields.io/badge/Estado-TFM_Completado-success.svg)

Este repositorio contiene el código fuente desarrollado como parte del **Trabajo Fin de Máster (TFM)** para la monitorización automatizada de variables ambientales y consumos hídricos/energéticos en infraestructuras públicas de la Comunidad Autónoma de Canarias.

## 📋 Descripción del Proyecto

El proyecto aborda dos retos críticos de la ingeniería actual en el despliegue de redes IoT industriales (IIoT):
1. El aislamiento de datos impuesto por dispositivos IoT comerciales (protocolos cerrados dependientes de nubes de terceros).
2. Las estrictas políticas de seguridad perimetral de TI en infraestructuras públicas (como filtrado de puertos o firewalls simétricos) que dificultan el uso de redes Wi-Fi locales compartidas.

Para solventar estas restricciones, se ha desarrollado una **Pasarela IoT Híbrida (Wi-Fi / 4G)** basada en microcontroladores ESP32 y módems celulares. El firmware orquesta un entorno de simulación multivariable que instrumenta un flujo unidireccional de datos emparejando modelos sintéticos teóricos con telemetría real. Este anclaje físico se logra mediante peticiones criptográficas seguras (HMAC-SHA256) contra la API de Tuya Cloud.

## 🏗️ Arquitectura del Sistema

El sistema se estructura en tres capas principales:
* **Capa Perimetral (Edge - MicroPython):** Firmware embebido con tolerancia a fallos de red mediante la implementación algorítmica del patrón *Store-and-Forward* local (en RAM) y conmutación automatizada entre redes Wi-Fi y canales celulares aislados (*Out-of-Band Gateway* mediante comandos AT/PPP).
* **Capa Analítica (Edge Computing - Node-RED):** Motor de procesamiento que incorpora rutinas de limpieza de ruido, unificación temporal de series y algoritmos de detección de anomalías locales (Z-Score estadístico, EWMA y Lógica Difusa orientada a ratios).
* **Capa de Visualización y Almacenamiento (Grafana / InfluxDB):** Repositorio determinista de series temporales acoplado a una interfaz de monitorización geoespacial (mapas de marcadores interactivos) y visualización cronológica forense del estado de la flota canaria.

## 💻 Requisitos de Hardware

* Microcontrolador: **LILYGO T-SIM7600G-H / A7670E** (o plataforma equivalente ESP32 con módem LTE integrado).
* Tarjeta SIM con APN configurado, datos activos y capacidad para gestión desatendida de PIN.
* Transductor de temperatura y humedad comercial del ecosistema Tuya (para el anclaje físico del modelo térmico local).

## ⚙️ Configuración y Uso

### 1. Variables de Entorno (Credenciales)
Por motivos de seguridad y buenas prácticas, las credenciales reales han sido ofuscadas en el repositorio. Antes de transferir el firmware al microcontrolador, es obligatorio configurar los parámetros de tu entorno local en la cabecera del script principal `codigo_wifi_gemelo_digital_flotaV3.py`:

```python
WIFI_SSID = "TU_SSID_WIFI"
WIFI_PASS = "TU_PASSWORD_WIFI"
ACCESS_ID = "TU_ACCESS_ID_TUYA_DEVELOPER"
ACCESS_SECRET = "TU_ACCESS_SECRET_TUYA"
DEVICE_ID = "TU_DEVICE_ID_FISICO"
APN_OPERADORA = "TU_APN" # Ejemplo: lowi.private.omv.es
