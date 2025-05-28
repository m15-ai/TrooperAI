# client.py
import asyncio
import pyaudio
import numpy as np
import queue
import json
import os
import websockets
import subprocess
import soxr
import time
import io
import wave
from utils import load_config, find_device, list_pyaudio_devices
import threading
from utils import led_request
from utils import apply_fade

# === Load Config ===
config = load_config()
fade_duration = config.get("fade_duration_ms", 0)

audio_q = queue.Queue()
playback_q = queue.Queue()

mic_was_muted = False  # shared state

async def send_audio(ws,config):
    # includes resampling for the Shure mic which only supports rate=48000
    while True:
        data = await asyncio.to_thread(audio_q.get)
        audio_np = data.flatten()  # ensure 1D
        rate = config.get("mic_rate", 48000)  # fallback to old default
        resampled_np = soxr.resample(audio_np, rate, 16000)
        clipped = np.clip(resampled_np, -32768, 32767).astype(np.int16)
        await ws.send(clipped.tobytes())


def audio_playback_worker(output_device_index, loop):
    global mic_stream
    global mic_was_muted

    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=2,
        rate=48000,
        output=True,
        output_device_index=output_device_index,
        frames_per_buffer=1024
    )
    stream.start_stream()

    while True:
        data = playback_q.get()
        if data is None:
            break  # Shutdown signal
        
        if data == "__END__":
            print("[Playback] Finished final chunk")

            # Reactivate mic here
            if MUTE_MIC and mic_stream and not mic_stream.is_active():
                print("[Mic] Reactivating mic")
                time.sleep(0.25)  # optional: wait 100ms for output device to drain
                mic_stream.start_stream()
                mic_was_muted = False
            else:
                print("[Mic] Could not reactivate — mic_stream is None or already active")

            # Still notify server or UI
            asyncio.run_coroutine_threadsafe(
                outgoing_ws.send("__done__"),
                loop
            )
            continue
        try:
            stream.write(data)
        except Exception as e:
            print(f"[Playback Error] {e}")
        time.sleep(0.001)

    stream.stop_stream()
    stream.close()
    p.terminate()


async def receive_audio(ws, config):
    global mic_stream
    global mic_was_muted

    buffer = bytearray()
    mic_was_muted = False
    fade_duration = config.get("fade_duration_ms", 0)
    is_first_chunk = True

    AUDIO_OUTPUT_DEVICE_INDEX = find_device(config.get("audio_output_device", ""), is_input=False)
    if AUDIO_OUTPUT_DEVICE_INDEX is None:
        print("[Warning] Using default output device")

    try:
        async for message in ws:
            if isinstance(message, bytes):
                buffer += message

                if len(buffer) >= 48000:
                    #print(f"[Client] Playing {len(buffer)} bytes")

                    if MUTE_MIC and mic_stream and mic_stream.is_active() and not mic_was_muted:
                        print("[Mic] Muting mic for playback")
                        mic_stream.stop_stream()
                        mic_was_muted = True

                    chunk = bytes(buffer)

                    # Apply fade-in to the first chunk
                    if is_first_chunk and fade_duration > 0:
                        chunk = apply_fade(chunk, fade_duration, apply_in=True, apply_out=False)
                        is_first_chunk = False

                    playback_q.put(chunk)
                    buffer = bytearray()

            elif isinstance(message, str) and message.strip() == "__END__":
                print("[Client] Received __END__")
                # Send remaining buffered audio with fade-out
                if buffer:
                    chunk = bytes(buffer)
                    if fade_duration > 0:
                        chunk = apply_fade(chunk, fade_duration, apply_in=False, apply_out=True)
                    playback_q.put(chunk)
                    buffer = bytearray()
                is_first_chunk = True
                playback_q.put("__END__")
    finally:
        pass


last_led_update = 0  # global or persistent variable
LED_DEBOUNCE_INTERVAL = 0.5  # seconds (500ms)

def mic_stream_callback(in_data, frame_count, time_info, status):
    global last_led_update
    audio_np = np.frombuffer(in_data, dtype=np.int16)
    audio_q.put(audio_np)
    #print("[Mic] Callback triggered")
    volume = np.abs(audio_np).mean()
    now = time.time()
    if volume > 750 and (now - last_led_update > LED_DEBOUNCE_INTERVAL):
        led_request("listen")
        last_led_update = now
    return (None, pyaudio.paContinue)


mic_stream = None  # Global reference for mic control

async def main():
    global mic_stream, MUTE_MIC

    config = load_config()
    list_pyaudio_devices()

    print(f"[Config] Looking for output device match: '{config['audio_output_device']}'")

    # Start PyAudio mic stream manually
    pa = pyaudio.PyAudio()

    CHUNK = 1024
    MIC_INDEX = find_device(config["mic_name"], is_input=True)
    if MIC_INDEX is None:
        print(f"[Error] Input device '{config['mic_name']}' not found. Please check mic connection.")
        return
    AUDIO_OUTPUT_DEVICE_INDEX = find_device(config.get("audio_output_device", ""), is_input=False)
    if AUDIO_OUTPUT_DEVICE_INDEX is None:
        print(f"[Error] Output device '{config['audio_output_device']}' not found. Please check speaker connection.")
        return
    DEVICE_INFO = pa.get_device_info_by_index(MIC_INDEX)
    RATE = int(DEVICE_INFO["defaultSampleRate"])
    MUTE_MIC = config.get("mute_mic_during_playback", True)
    config["mic_rate"] = RATE  # Inject it into config for use elsewhere
    print(f"[Debug] Using mic sample rate: {RATE} Hz")

    volume = config.get("volume")
    if isinstance(volume, int) and 0 <= volume <= 100:
        print(f"[Audio] Setting volume to {volume}%")
        try:
            subprocess.run(["amixer", "set", "Master", f"{volume}%"], check=True)
        except Exception as e:
            print(f"[Warning] Failed to set volume: {e}")

    mic_stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        input=True,
        input_device_index=MIC_INDEX,
        frames_per_buffer=CHUNK,
        stream_callback=mic_stream_callback
    )

    mic_stream.start_stream()

    uri = "ws://localhost:8765"
    async with websockets.connect(
        uri,
        ping_timeout=120,
        ping_interval=30
    ) as ws:    
        print("[Client] Connected to WebSocket server.")

        loop = asyncio.get_running_loop()
        global outgoing_ws
        outgoing_ws = ws  # still needed globally

        playback_thread = threading.Thread(
            target=audio_playback_worker,
            args=(AUDIO_OUTPUT_DEVICE_INDEX, loop),  # pass the loop
            daemon=True
        )
        playback_thread.start()

        await asyncio.gather(
            send_audio(ws, config),
            receive_audio(ws, config)
        )
    mic_stream.stop_stream()
    mic_stream.close()
    pa.terminate()

    playback_q.put(None)
    playback_thread.join()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[Client] Exiting.")
