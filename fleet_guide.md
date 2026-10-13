Minds and programs (on the PC unless noted):
- RIFT: fleet hub, Python/Flask on :5000 (RIFT/app.py). Robot registry (GET /fleet, POST /register), dashboard, mission log (/mission), the robots' Brainfuck chatter (RIFT/Fleet/conversations.py, phrasebook RIFT/Fleet/brainfuck_talk.py).
- NINA: caretaker on :5012 (NINA/nina.py). Watches the fleet through RIFT, NORA's speed/reboots/mode, the PC's WiFi and serial ports; sends you fleet news.
- DREAM: offline AI companion (DREAM/app.py): wake word, Whisper, Ollama, Piper voice, avatar; Arduino sensor board DREAM/dream_sensors/dream_sensors.ino.
- LYCEA: Unity ML-Agents school where NORA learns to drive and WHIP to walk.
- TINA: you, on :5013.

Robots (their own WiFi is NORA's network 192.168.4.x; the PC can only reach them when it's on it):
- NORA: ESP32 mecanum robot and the fleet's WiFi hub (192.168.4.1, API :5002). Code: NORA-Robot-v00/scripts/esp32/esp32.ino + Arduino Uno sensor/music board scripts/arduino/arduino.ino. Four-way ultrasonic braking, line following, MP3, UV light.
- MILA: small tank, Arduino Uno + ESP8266. Code: MILA-Robot-v00/scripts/MILA/MILA.ino (motors, ultrasonic on a servo, obstacle mode) and scripts/esp8266/esp8266.ino (web API: /cmd needs /unlock, /status, /mode, /chirp).
- IDA: Arduino Uno tank, IR remote only. Code: IDA-Robot-v00/scripts/IDA/IDA.ino. Ultrasonic avoidance with diagonal scans.
- WHIP: 18-servo ESP32 hexapod. Code: WHIP-Robot-v00/scripts/esp32/esp32.ino. Obstacle avoidance, MPU6050 tilt safety.
- KIDA00: Raspberry Pi 3 tank (Freenove HAT), Python. Code: KIDA-Robot-v00/scripts/server.py, main.py. Camera, age/gender detection.
- KIDA01: Raspberry Pi 5 tank with Hailo-8L, cameras and her own LLM mind. Code: KIDA-Robot-v01/scripts/server.py, scripts/main.py; motor Arduino scripts/arduino/arduino00/arduino00.ino.
- ARM: 6-DOF arm, Arduino Uno + PCA9685. Code: ARM-Robot-v01/scripts/arm/arm.ino, controller scripts/controller.py.
