import json
import os
import pyaudio
import errno
import numpy as np

def load_config():
    DEFAULTS = {
        "volume": 90,
        "session_timeout": 300,
        "greeting_message": "Identify yourdelf.",
        "mic_name": "USB Audio",
        "audio_output_device": "USB Audio",
        "closing_message": "Mission abandoned. Shutting down.",
        "voice": "danny-low.onnx",
        "mute_mic_during_playback": True,
        "model_name": "gemma3:1b",
        "system_prompt": "You are a loyal Imperial Stormtrooper. Be blunt. Short answers only.",
        "history_length": 6,
        "max_response_characters": 350,
        "enable_audio_processing": False,
        "vision_wake": True
    }

    USB_PATH = "/media/mjw/TROOPER/trooper_config.json"
    LOCAL_PATH = os.path.expanduser(".trooper_config.json")

    try:
        if os.path.exists(USB_PATH):
            print("[Config] Loading from USB...")
            with open(USB_PATH, "r") as f:
                cfg = json.load(f)
            # Save to local config path
            with open(LOCAL_PATH, "w") as out:
                json.dump(cfg, out, indent=2)
            return {**DEFAULTS, **cfg}

        # Otherwise, load from local file
        if os.path.exists(LOCAL_PATH):
            with open(LOCAL_PATH, "r") as f:
                cfg = json.load(f)
                print("[Config] Loaded from local file.")
            return {**DEFAULTS, **cfg}

    except Exception as e:
        print(f"[Config] Error loading config: {e}")

    return DEFAULTS
    
def list_pyaudio_devices():
    print("\n[PyAudio Devices]")
    pa = pyaudio.PyAudio()
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        name = info.get("name", "Unknown")
        inputs = info.get("maxInputChannels", 0)
        outputs = info.get("maxOutputChannels", 0)
        print(f"  [{i}] {name} | in: {inputs}ch  out: {outputs}ch")
    pa.terminate()

def find_device(target_name, is_input=True):
    pa = pyaudio.PyAudio()
    target_name = target_name.lower()
    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        name = info.get("name", "").lower()
        if target_name in name:
            if is_input and info.get("maxInputChannels", 0) > 0:
                print(f"[Device] Found input #{i}: {info['name']}")
                pa.terminate()
                return i
            elif not is_input and info.get("maxOutputChannels", 0) > 0:
                print(f"[Device] Found output #{i}: {info['name']}")
                pa.terminate()
                return i
    pa.terminate()
    print(f"[Device] No match for {'input' if is_input else 'output'} '{target_name}', using default.")
    return None

def led_request(mode):
    """Send a blink mode to the trooper LED FIFO pipe."""
    try:
        fd = os.open("/tmp/trooper_led", os.O_WRONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "w") as fifo:
            fifo.write(mode + "\n")
    except OSError as e:
        if e.errno == errno.ENXIO:
            pass  # no reader
        else:
            print(f"[LED] Error: {e}")

def apply_fade(audio_bytes, fade_ms, sample_rate=48000, channels=2, apply_in=True, apply_out=True):
    if fade_ms == 0 or not (apply_in or apply_out):
        return audio_bytes

    fade_samples = int((fade_ms / 1000.0) * sample_rate)
    total_samples = len(audio_bytes) // 2  # int16 = 2 bytes

    if total_samples < 2 * fade_samples:
        return audio_bytes

    audio = np.frombuffer(audio_bytes, dtype=np.int16).copy()

    if apply_in:
        fade_in = np.linspace(0.0, 1.0, fade_samples)
        for i in range(fade_samples):
            audio[i * channels:(i + 1) * channels] = (
                audio[i * channels:(i + 1) * channels] * fade_in[i]
            ).astype(np.int16)

    if apply_out:
        fade_out = np.linspace(1.0, 0.0, fade_samples)
        for i in range(fade_samples):
            audio[-(i + 1) * channels:-(i) * channels if i > 0 else None] = (
                audio[-(i + 1) * channels:-(i) * channels if i > 0 else None] * fade_out[i]
            ).astype(np.int16)

    return audio.tobytes()
