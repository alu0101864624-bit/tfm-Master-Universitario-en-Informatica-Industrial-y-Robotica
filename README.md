# tfm-Master-Universitario-en-Informatica-Industrial-y-Robotica
Gateway IoT híbrido con gemelos digitales para infraestructuras canarias
# 🌍 IoT-Hybrid-Gateway: Gemelo Digital para Infraestructuras Canarias

![MicroPython](https://img.shields.io/badge/MicroPython-1.19+-blue.svg)
![MQTT](https://img.shields.io/badge/Protocol-MQTT-yellow.svg)
![Hardware](https://img.shields.io/badge/Hardware-LILYGO_T--SIM7600-green.svg)
![Estado](https://img.shields.io/badge/Estado-TFM_Completado-success.svg)

Este repositorio contiene el código fuente desarrollado como parte del **Trabajo Fin de Máster (TFM)** para la monitorización automatizada de variables ambientales y consumos hídricos/energéticos en infraestructuras públicas de la Comunidad Autónoma de Canarias.

## 📋 Descripción del Proyecto

El proyecto aborda dos retos críticos de la ingeniería actual:
1. El aislamiento de datos impuesto por dispositivos IoT comerciales (protocolos cerrados en la nube).
2. Las estrictas políticas perimetrales de TI en edificios públicos que impiden el despliegue de sensores en redes Wi-Fi locales.

Para solucionarlo, se ha desarrollado una **Pasarela IoT Híbrida (Wi-Fi / 4G)** basada en microcontroladores ESP32 y módems celulares. El firmware orquesta un entorno de simulación avanzado (**Gemelo Digital basado en la física**) que inyecta telemetría estocástica combinada con datos reales anclados mediante criptografía (HMAC-SHA256) contra la API de Tuya Cloud.

## 🏗️ Arquitectura del Sistema

El sistema se divide en tres capas principales:
* **Capa Edge (MicroPython):** Firmware embebido con capacidad *Store-and-Forward* para tolerancia a fallos y conmutación transparente entre redes Wi-Fi y celulares (PPP/AT Commands).
* **Capa Core (Docker / Node-RED / MQTT):** Motor de ingesta asíncrona que incluye un **Módulo de Normalización Semántica** para estandarizar *payloads* heterogéneos y proteger la base de datos temporal (InfluxDB).
* **Capa de Supervisión (Grafana):** Cuadros de mando desarrollados con lenguaje Flux para la detección temprana de anomalías térmicas e hídricas (Edge AI).

## 💻 Requisitos de Hardware

* Microcontrolador: **LILYGO T-SIM7600G-H** (o equivalente ESP32-WROVER con módem LTE).
* Tarjeta SIM con APN configurado y datos activos (para modo 4G).
* Transductor de temperatura y humedad del ecosistema Tuya (para anclaje del modelo físico).

## ⚙️ Configuración y Uso

### 1. Variables de Entorno (Credenciales)
Por motivos de seguridad, las credenciales reales han sido ofuscadas. Antes de flashear el microcontrolador, debes rellenar los datos de tu entorno en la cabecera del script `gemelo_digital_hibrido_flotaV3.py`:

```python
WIFI_SSID = "TU_SSID_WIFI"
WIFI_PASS = "TU_PASSWORD_WIFI"
ACCESS_ID = "TU_ACCESS_ID_TUYA"
ACCESS_SECRET = "TU_ACCESS_SECRET_TUYA"
DEVICE_ID = "TU_DEVICE_ID"
APN_OPERADORA = "TU_APN"
