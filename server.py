# server.py
import asyncio
import websockets
import json
import subprocess
import io
import time
import os
import re
from vosk import Model, KaldiRecognizer
from utils import load_config, led_request

RATE = 16000
CHANNELS = 1
MODEL_PATH = "vosk-model"
PIPER_PATH = "/home/mjw/.local/bin/piper"

LOW_EFFORT_UTTERANCES = {"huh", "uh", "um", "erm", "hmm", "he's", "but", "the"}

vosk_model = Model(MODEL_PATH)

def clean_response(text):
    text = re.sub(r"[\*]+", '', text)
    text = re.sub(r"\(.*?\)", '', text)
    text = re.sub(r"<.*?>", '', text)
    text = text.replace('\n', ' ').strip()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+', '', text)
    return text

async def query_ollama(model, messages):
    payload = {
        "model": model,
        "messages": messages,
        "stream": False
    }
    proc = await asyncio.create_subprocess_exec(
        'curl', '-s', '-X', 'POST', 'http://localhost:11434/api/chat',
        '-H', 'Content-Type: application/json',
        '-d', json.dumps(payload),
        stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    result = json.loads(stdout)
    return result['message']['content'].strip()

async def stream_ollama_response(model, messages):
    payload = {
        "model": model,
        "messages": messages,
        "stream": True
    }
    proc = await asyncio.create_subprocess_exec(
        'curl', '-N', '-s', '-X', 'POST', 'http://localhost:11434/api/chat',
        '-H', 'Content-Type: application/json',
        '-d', json.dumps(payload),
        stdout=asyncio.subprocess.PIPE
    )

    async for line in proc.stdout:
        chunk = line.decode().strip()
        if not chunk:
            continue
        try:
            json_chunk = json.loads(chunk)
            token = json_chunk.get("message", {}).get("content", "")
            yield token
        except json.JSONDecodeError:
            continue

async def stream_tts(text, voice, retro_voice_fx):
    voice_model_path = f"voices/{voice}"

    piper_proc = await asyncio.create_subprocess_exec(
        PIPER_PATH, '--model', voice_model_path, '--output_raw',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE
    )
    piper_proc.stdin.write(text.encode() + b'\n')
    await piper_proc.stdin.drain()
    piper_proc.stdin.close()
    raw_pcm = await piper_proc.stdout.read()
    await piper_proc.wait()

    sox_cmd = [
        "sox",
        "-t", "raw", "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", "-",
        "-r", "48000", "-c", "2", "-t", "raw", "-"
    ]
    if retro_voice_fx:
        sox_cmd += [
            "highpass", "300", "lowpass", "3400",
            "compand", "0.3,1", "6:-70,-60,-20", "-5", "-90", "0.2",
            "gain", "-n", "vol", "0.9",
            "synth", "brownnoise", "mix", "0.01"
        ]

    sox_proc = await asyncio.create_subprocess_exec(
        *sox_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE
    )
    sox_stdout, _ = await sox_proc.communicate(input=raw_pcm)

    silence = b"\x00" * 19200
    sox_stdout = silence + sox_stdout

    for i in range(0, len(sox_stdout), 2048):
        yield sox_stdout[i:i+2048]

async def process_connection(websocket):
    recognizer = KaldiRecognizer(vosk_model, RATE)
    session_config = None

    async for message in websocket:
        if isinstance(message, str):
            try:
                data = json.loads(message)
                if data.get("type") == "config_sync":
                    session_config = data.get("config", {})
                    print("[Server] Config synced:", session_config.get("voice"))
                    continue
            except Exception as e:
                print("[Server] Failed to load config:", e)
                continue

            if message == "__done__":
                led_request("solid")
                continue

        if session_config is None:
            continue  # wait until config is set

        if recognizer.AcceptWaveform(message):
            result = json.loads(recognizer.Result())
            user_text = result.get("text", "").strip()
            if not user_text:
                continue

            cleaned = user_text.lower().strip(".,!? ")
            if cleaned in LOW_EFFORT_UTTERANCES:
                recognizer = KaldiRecognizer(vosk_model, RATE)
                continue

            print("[User]:", user_text)
            messages = [{"role": "system", "content": session_config.get("system_prompt", "")}]
            messages.append({"role": "user", "content": user_text})
            led_request("blink")

            context = [messages[0]] + messages[-session_config.get("history_length", 0):]
            full_response = ""
            response_text = ""

            async for token in stream_ollama_response(session_config["model_name"], context):
                response_text += token
                if token.endswith((".", "!", "?", "\n")):
                    segment = clean_response(response_text).strip()
                    if segment and not re.fullmatch(r"[.?!\-–—…]+", segment):
                        print(f"[Trooper]: {segment}")
                        full_response += segment + " "
                        led_request("speak")
                        async for chunk in stream_tts(segment, session_config["voice"], session_config.get("retro_voice_fx", False)):
                            await websocket.send(chunk)
                    response_text = ""

            if response_text.strip():
                segment = clean_response(response_text).strip()
                full_response += segment + " "
                async for chunk in stream_tts(segment, session_config["voice"], session_config.get("retro_voice_fx", False)):
                    await websocket.send(chunk)

            await websocket.send("__END__")
            led_request("solid")

async def main():
    print("[Server] Listening on ws://0.0.0.0:8765 ...")
    async with websockets.serve(process_connection, "0.0.0.0", 8765, ping_timeout=None, ping_interval=None):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())


