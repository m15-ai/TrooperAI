# server.py
import asyncio
import websockets
import json
import subprocess
import io
import errno
import time
import os
import re
from vosk import Model, KaldiRecognizer
from utils import load_config, find_device, list_pyaudio_devices
from utils import led_request

# === Load Config ===
config = load_config()

# === Constants ===
RATE = 16000
CHANNELS = 1
MODEL_PATH = "vosk-model"
OLLAMA_URL = "http://localhost:11434/api/chat"
VOICE_MODEL_PATH = f"voices/{config['voice']}"
PIPER_PATH = "/home/mjw/.local/bin/piper"
OLLAMA_MODEL = config["model_name"]
HISTORY_LENGTH = config["history_length"]
MAX_RESPONSE_CHARS = config.get("max_response_chars", 300)
RETRO_VOICE_ENABLED = config.get("retro_voice_fx", False)


LOW_EFFORT_UTTERANCES = {"huh", "uh", "um", "erm", "hmm", "he's", "but", "the"}

# === Load Vosk model once at startup ===
vosk_model = Model(MODEL_PATH)

async def query_ollama(messages):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False
    }

    proc = await asyncio.create_subprocess_exec(
        'curl', '-s', '-X', 'POST', OLLAMA_URL,
        '-H', 'Content-Type: application/json',
        '-d', json.dumps(payload),
        stdout=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    result = json.loads(stdout)
    return result['message']['content'].strip()


async def stream_tts(text):
    #print(f"[TTS] Streaming: {text}")

    # Generate raw PCM from Piper
    piper_proc = await asyncio.create_subprocess_exec(
        PIPER_PATH, '--model', VOICE_MODEL_PATH, '--output_raw',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE
    )
    piper_proc.stdin.write(text.encode() + b'\n')  # newline triggers Piper

    await piper_proc.stdin.drain()
    piper_proc.stdin.close()

    # Read raw PCM output from Piper
    raw_pcm = await piper_proc.stdout.read()  # read all
    await piper_proc.wait()

    if RETRO_VOICE_ENABLED:
        sox_cmd = [
            "sox",
            "-t", "raw", "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", "-",
            "-r", "48000", "-c", "2", "-t", "raw", "-",
            "highpass", "300", "lowpass", "3400",
            "compand", "0.3,1", "6:-70,-60,-20", "-5", "-90", "0.2",
            "gain", "-n", "vol", "0.9",
            "synth", "brownnoise", "mix", "0.01"
        ]
    else:
        sox_cmd = [
            "sox",
            "-t", "raw", "-r", "16000", "-c", "1", "-b", "16", "-e", "signed-integer", "-",
            "-r", "48000", "-c", "2", "-t", "raw", "-"
        ]

    sox_proc = await asyncio.create_subprocess_exec(
        *sox_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE
    )

    sox_stdout, _ = await sox_proc.communicate(input=raw_pcm)

    # Add before yielding chunks to help with clipping of the first word
    silence = b"\x00" * 19200
    sox_stdout = silence + sox_stdout

    #print(f"[TTS] SoX output size: {len(sox_stdout)} bytes")
    #with open("final_playback.raw", "wb") as f:
    #    f.write(sox_stdout)    

    # Yield audio in chunks
    chunk_size = 2048
    for i in range(0, len(sox_stdout), chunk_size):
        yield sox_stdout[i:i+chunk_size]

def clean_response(text):
    text = re.sub(r"[\*]+", '', text)                 # remove asterisks
    text = re.sub(r"\(.*?\)", '', text)               # remove stage directions
    text = re.sub(r"<.*?>", '', text)                 # remove HTML tags
    text = text.replace('\n', ' ').strip()            # normalize newlines
    text = re.sub(r'\s+', ' ', text)                  # collapse whitespace
    text = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+', '', text)  # remove emojis
    return text

async def process_connection(websocket):
    recognizer = KaldiRecognizer(vosk_model, RATE)
    messages = [{"role": "system", "content": config["system_prompt"]}]
    print("[Server] Client connected.")

    try:
        async for message in websocket:
            if not isinstance(message, bytes):
                #print(f"[Server] Non-audio message received: {message}")
                if message == "__done__":
                    #print("[Server] Received __done__ — setting LED to solid")
                    led_request("solid")
                continue

            stt_start = time.time()
            if recognizer.AcceptWaveform(message):
                result = json.loads(recognizer.Result())
                user_text = result.get("text", "").strip()

                if not user_text:
                    continue

                cleaned = user_text.lower().strip(".,!? ")
                if cleaned in LOW_EFFORT_UTTERANCES:
                    print("[Debug] Ignored low-effort utterance.")
                    recognizer = KaldiRecognizer(vosk_model, RATE)  # reset state
                    continue

                #print("[STT Final]:", user_text)
                stt_ms = int((time.time() - stt_start) * 1000)
                print("[User]:", user_text)
                messages.append({"role": "user", "content": user_text})

                # Query LLM
                led_request("blink")  # Slow blink while LLM is thinking
                context = [messages[0]] + messages[-HISTORY_LENGTH:]
                
                #print("[TTS] stream_tts_from_ollama entered")

                full_response = "" # Keep the assistance response cumulative
                response_text = ""
                segment = ""
                llm_tts_start = time.time()

                async for token in stream_ollama_response(context):
                    if token:
                        response_text += token
                        #print(f"[LLM token]: {token.strip()}")
                        
                        # Sentence streaming to Piper
                        if token.endswith((".", "!", "?", "\n")):
                            segment = clean_response(response_text).strip()

                            # Skip empty or punctuation-only segments
                            if segment and not re.fullmatch(r"[.?!\-–—…]+", segment):
                                print(f"[Trooper]: {segment}")
                                full_response += segment + " "
                                led_request("speak")
                                async for chunk in stream_tts(segment):
                                    await websocket.send(chunk)

                            response_text = ""  # always reset

                # Final flush
                if response_text.strip():
                    segment = clean_response(response_text).strip()
                    #print(f"[TTS] Final sentence: {segment}")

                    full_response += segment + " "

                    async for chunk in stream_tts(segment):
                        try:
                            await websocket.send(chunk)
                        except websockets.ConnectionClosed:
                            print("[Server] WebSocket closed mid-response.")
                            return

                # Notify client that TTS is complete
                await websocket.send("__END__")

                llm_tts_ms = int((time.time() - llm_tts_start) * 1000)
                messages.append({"role": "assistant", "content": full_response.strip()})
                print(f"Inference timings (STT/LLM/TTS) : {stt_ms} ms / {llm_tts_ms} ms")

    except websockets.exceptions.ConnectionClosed:
        print("[Server] Client disconnected.")


async def stream_ollama_response(messages):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True
    }

    proc = await asyncio.create_subprocess_exec(
        'curl', '-N', '-s', '-X', 'POST', OLLAMA_URL,
        '-H', 'Content-Type: application/json',
        '-d', json.dumps(payload),
        stdout=asyncio.subprocess.PIPE
    )

    buffer = ""
    async for line in proc.stdout:
        chunk = line.decode().strip()
        #print(f"[Ollama] Raw line: {chunk}")

        if not chunk:
            continue

        try:
            json_chunk = json.loads(chunk)  # Don't strip 'data:' — Ollama doesn't include it
            #print(f"[Ollama] Parsed JSON: {json_chunk}")

            token = json_chunk.get("message", {}).get("content", "")
            #print(f"[Ollama] Token: {token}")
            yield token
        except json.JSONDecodeError as e:
            print(f"[Ollama] JSON decode error: {e} | chunk: {chunk}")


async def main():
    print("[Server] Listening on ws://0.0.0.0:8765 ...")
    async with websockets.serve(
        process_connection,
        "0.0.0.0",
        8765,
        ping_timeout=None, # how long to wait for pong
        ping_interval=None  # how often to ping
    ):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())

