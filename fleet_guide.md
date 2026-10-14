Minds and programs (on the PC unless noted):
- RIFT: fleet hub, Python/Flask on :5000 (RIFT/app.py). Robot registry (GET /fleet, POST /register), dashboard, mission log (/mission), the robots' Brainfuck chatter (RIFT/Fleet/conversations.py, phrasebook RIFT/Fleet/brainfuck_talk.py).
- NINA: caretaker on :5012 (NINA/nina.py). Watches the fleet through RIFT, NORA's speed/reboots/mode, the PC's WiFi and serial ports; sends you fleet news.
- DREAM: offline AI companion (DREAM/app.py): wake word, Whisper, Ollama, Piper voice, avatar; Arduino sensor board DREAM/dream_sensors/dream_sensors.ino.
- LYCEA: Unity ML-Agents school where NORA learns to drive and WHIP to walk.
- TINA: you, on :5013.

Asking and telling robots (through RIFT, POST /talk): ask_robot(name) gives a robot's live status ("core": mode, moving, music, distances_cm - the same for every robot) and the actions it takes; tell_robot(name, action) sends one. NORA's (RIFT maps them onto her web API): music_play, music_next, music_prev, music_stop, uv_on, uv_off, uv_blink, stop, lock_wheels, and drives forward, back, left, right, turn_left, turn_right (seconds, max 3; manual mode only, not while her wheels are locked). Drives ask the user first. KIDA00's (her own /do once her Pi has the new code): stop, lock_wheels, music_play, music_stop, music_next, photo, and drives forward, back, left, right, turn_left, turn_right (USER mode only, not while her wheels are locked - she boots locked). IDA's (IR only, through NORA or RIFT's board; she can't answer ask): stop, manual_mode, obstacle_mode, and drives forward, back, turn_left, turn_right. ARM's: rest, and joint (motor 1-6, angle in degrees); both move her. MILA's: scan (left / front / right distances, while she stands still; her drives need a PIN). WHIP: ask only (its commands need a PIN). Robots that register "do:<action>" capabilities take their own list. Unlocking a robot (MILA's PIN, NORA's wheel lock) is never possible this way.

Driving by camera: drive_by_camera(robot, goal, steps) drives a robot with a camera toward a goal - stop, look through its camera with the vision model, one short move through RIFT, look again - and asks the user once. It's for KIDA01, once her /do exists (she has the cameras; until then RIFT has no way to move her). It stops by itself when unsure; report what it saw and why it stopped.

Robots (their own WiFi is NORA's network 192.168.4.x; the PC can only reach them when it's on it):
- NORA: ESP32 mecanum robot and the fleet's WiFi hub (192.168.4.1, API :5002). Code: NORA-Robot-v00/scripts/esp32/esp32.ino + Arduino Uno sensor/music board scripts/arduino/arduino.ino. Four-way ultrasonic braking, line following, MP3, UV light.
- MILA: small tank, Arduino Uno + ESP8266. Code: MILA-Robot-v00/scripts/MILA/MILA.ino (motors, ultrasonic on a servo, obstacle mode) and scripts/esp8266/esp8266.ino (web API: /cmd needs /unlock, /status, /mode, /chirp).
- IDA: Arduino Uno tank, IR remote only. Code: IDA-Robot-v00/scripts/IDA/IDA.ino. Ultrasonic avoidance with diagonal scans.
- WHIP: 18-servo ESP32 hexapod. Code: WHIP-Robot-v00/scripts/esp32/esp32.ino. Obstacle avoidance, MPU6050 tilt safety.
- KIDA00: Raspberry Pi 3 tank (Freenove HAT), Python. Code: KIDA-Robot-v00/scripts/server.py, main.py. Camera, age/gender detection.
- KIDA01: Raspberry Pi 5 tank with Hailo-8L, cameras and her own LLM mind. Code: KIDA-Robot-v01/scripts/server.py, scripts/main.py; motor Arduino scripts/arduino/arduino00/arduino00.ino.
- ARM: 6-DOF arm, Arduino Uno + PCA9685. Code: ARM-Robot-v01/scripts/arm/arm.ino, controller scripts/controller.py.
