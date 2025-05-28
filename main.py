# main.py
import time
import os
import threading
import subprocess
import json
from gpiozero.pins.lgpio import LGPIOFactory
from gpiozero import Device, Button, LED
from signal import pause
import cv2
import mediapipe as mp
import wave
import io
import pyaudio
from utils import load_config, find_device
import asyncio
import aiohttp

Device.pin_factory = LGPIOFactory()

FIFO_PATH = "/tmp/trooper_led"
if not os.path.exists(FIFO_PATH):
    os.mkfifo(FIFO_PATH)

BUTTON_PIN = 17
LED_PIN = 18

button = Button(BUTTON_PIN, pull_up=True, hold_time=0.75)

led = LED(LED_PIN, active_high=False)

client_proc = None
session_active = [False]  # mutable shared state

timeout_thread = None

def led_pipe_listener():
    while True:
        with open(FIFO_PATH, "r") as fifo:
            for line in fifo:
                mode = line.strip()
                if mode:
                    #print(f"[LED] Received mode: {mode}")
                    led_mode(mode)

threading.Thread(target=led_pipe_listener, daemon=True).start()

def play_message(text):
    config = load_config()
    voice_model = config.get("voice", "danny-low.onnx")
    device_name = config.get("audio_output_device", "")
    AUDIO_OUTPUT_DEVICE_INDEX = find_device(device_name, is_input=False)
    retro_fx = config.get("retro_voice_fx", False)

    print(f"[Debug] Playing message: '{text}' to device index {AUDIO_OUTPUT_DEVICE_INDEX}")

    # Generate raw PCM from Piper
    proc = subprocess.Popen(
        ["/home/mjw/.local/bin/piper", '--model', f'voices/{voice_model}', '--output_raw'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    raw_pcm, err = proc.communicate(input=text.encode())
    if err:
        print("[Piper Error]", err.decode())

    # Choose SoX pipeline
    if retro_fx:
        sox_cmd = [
            'sox', '-t', 'raw', '-r', '16000', '-c', '1', '-b', '16',
            '-e', 'signed-integer', '-', '-r', '48000', '-c', '2', '-t', 'wav', '-',
            'highpass', '300', 'lowpass', '3400',
            'compand', '0.3,1', '6:-70,-60,-20', '-5', '-90', '0.2',
            'gain', '-n', 'vol', '0.9',
            'synth', 'brownnoise', 'mix', '0.01'
        ]
    else:
        sox_cmd = [
            'sox', '-t', 'raw', '-r', '16000', '-c', '1', '-b', '16',
            '-e', 'signed-integer', '-', '-r', '48000', '-c', '2', '-t', 'wav', '-'
        ]    

    # Pipe PCM through SoX to resample to 48000Hz stereo WAV
    sox = subprocess.Popen(
        sox_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    wav_bytes, sox_err = sox.communicate(input=raw_pcm)
    if sox_err:
        print("[SoX Error]", sox_err.decode())

    # Playback using PyAudio
    wf = wave.open(io.BytesIO(wav_bytes), 'rb')
    p = pyaudio.PyAudio()
    stream = p.open(
        format=p.get_format_from_width(wf.getsampwidth()),
        channels=wf.getnchannels(),
        rate=wf.getframerate(),
        output=True,
        output_device_index=AUDIO_OUTPUT_DEVICE_INDEX
    )

    data = wf.readframes(1024)
    while data:
        stream.write(data)
        data = wf.readframes(1024)

    stream.stop_stream()
    stream.close()
    p.terminate()
    wf.close()

def led_mode(mode):
    led.off()  # ⬅️ Ensure we reset state before reconfiguring

    if mode == "off":
        led.off()
    elif mode == "solid":
        led.on()
    elif mode == "blink":
        led.blink(on_time=0.08, off_time=0.08) # slow blink LLM
    elif mode == "speak":
        led.blink(on_time=0.4, off_time=0.3) # fast blink Piper
    elif mode == "listen":
        led.blink(on_time=0.15, off_time=0.15) # user speaking

def session_loop():
    global client_proc
    config = load_config()
    greeting_msg = config.get("greeting_message", "").strip()
    timeout_msg = config.get("timeout_message", "").strip()
    timeout_sec = config.get("session_timeout", 0)

    
    # Pre-spin Ollama in the background to make if seem faster for the user's first prompt
    model_name = config.get("model_name")
    if model_name:
        threading.Thread(target=spin_up_ollama, args=(model_name,), daemon=True).start()

    print("[Trooper] Booting up.")
    led_mode("blink")
    if greeting_msg:
        play_message(greeting_msg)
    led_mode("solid")

    print("[Debug] Greeting complete, launching client.")

    log_file = open("./client.log", "w")
    client_proc = subprocess.Popen(
        ["python3", "client.py"],
        stdout=log_file,
        stderr=subprocess.STDOUT
    )
    print("[Debug] client.py launched.")

    def monitor_timeout(timeout_sec):
        if timeout_sec <= 0:
            return
        print(f"[Timeout] Session timeout armed for {timeout_sec} seconds.")
        time.sleep(timeout_sec)
        if session_active[0]:
            print("[Timeout] Session timeout expired. Ending session.")
            session_active[0] = False
            end_session(timeout_msg)

    global timeout_thread

    if timeout_thread and timeout_thread.is_alive():
        print("[Debug] Timeout thread already running — skipping.")
    else:
        timeout_thread = threading.Thread(target=monitor_timeout, args=(timeout_sec,), daemon=True)
        timeout_thread.start()

def end_session(msg):
    global client_proc

    if client_proc:
        print("[Trooper] Session ended.")
        client_proc.terminate()
        client_proc.wait()
        led_mode("off")
        if msg:
            play_message(msg)
        time.sleep(1)

def on_button_press():
    config = load_config()
    closing_msg = config.get("closing_message", "").strip()
    if not session_active[0]:
        session_active[0] = True
        session_loop()
    else:
        session_active[0] = False
        end_session(closing_msg)

def on_tap():
    print("[Button] Ignored short press")        

def spin_up_ollama(model):
    async def warmup():
        print(f"[Init] Warming up Ollama model: {model}")
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": "System check."}],
            "stream": True
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post("http://localhost:11434/api/chat", json=payload) as resp:
                    async for line in resp.content:
                        if line:
                            print("[Init] Ollama model ready.")
                            break
        except Exception as e:
            print(f"[Init] Ollama warmup failed: {e}")

    asyncio.run(warmup())


def vision_watch_loop():
    print("[Vision] Watching for raised hand (MediaPipe)...")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("[Vision] Could not open camera.")
        return

    mp_hands = mp.solutions.hands
    hands = mp_hands.Hands(static_image_mode=False, max_num_hands=1, min_detection_confidence=0.6)

    open_streak = 0
    required_streak = 5
    cooldown_seconds = 10
    last_toggle = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = hands.process(rgb)

        if results.multi_hand_landmarks:
            hand = results.multi_hand_landmarks[0]

            fingertips = [
                mp_hands.HandLandmark.THUMB_TIP,
                mp_hands.HandLandmark.INDEX_FINGER_TIP,
                mp_hands.HandLandmark.MIDDLE_FINGER_TIP,
                mp_hands.HandLandmark.RING_FINGER_TIP,
                mp_hands.HandLandmark.PINKY_TIP,
            ]

            up_count = 0
            for tip in fingertips:
                tip_y = hand.landmark[tip].y
                wrist_y = hand.landmark[mp_hands.HandLandmark.WRIST].y
                if tip_y < wrist_y:
                    up_count += 1

            print(f"[Debug] Fingers up: {up_count}, streak: {open_streak}")

            now = time.time()

            if up_count == 5:
                open_streak += 1
            else:
                open_streak = 0

            if open_streak >= required_streak and now - last_toggle > cooldown_seconds:
                print("[Gesture] Open hand detected — toggling session.")
                on_button_press()
                last_toggle = now
                open_streak = 0  # reset after toggle

        time.sleep(0.3)

button.when_held = on_button_press
button.when_released = on_tap

print("[System] Awaiting button press...")

config = load_config()

if config.get("vision_wake", False):
    threading.Thread(target=vision_watch_loop, daemon=True).start()
    print("[System] Hand-raise wake active.")
else:
    print("[System] Vision wake disabled in config.")

pause()
