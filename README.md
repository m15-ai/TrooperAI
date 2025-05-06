# 🥷 Trooper AI

**Trooper AI** is a low-latency, voice-driven chatbot powered by local speech-to-text (Vosk), LLM-based text generation (via Ollama), neural text-to-speech (Piper), and audio effects (SoX). The assistant embodies an **Imperial Stormtrooper** — blunt, loyal, and unyielding.

Built for **Raspberry Pi 5**, this project turns your device into a fully autonomous voice unit — no cloud required, no keyboard necessary.

---

## 🎯 Features

- 🧠 **Offline STT** via [Vosk](https://alphacephei.com/vosk/)
- 🤖 **LLM Text Generation** via [Ollama](https://ollama.com/) (`gemma:2b`, `qwen`, etc.)
- 🗣️ **Neural TTS** via [Piper](https://github.com/rhasspy/piper)
- 🎛️ **Voice FX Pipeline** with SoX:
  - Synthesized white noise
  - Bandpass filtering
  - Mixing for analog "comms" effect
- ⏱️ **Full Timing Metrics** printed per turn
- 🎤 **Live mic input / speaker output**
- 🧼 **Clean CLI logging** with no SoX warnings
- 👮‍♂️ Stormtrooper persona: cold, efficient, and direct

---

## 🛠️ System Requirements

- Raspberry Pi 5 (8GB recommended)
- Python 3.11+
- Vosk model (e.g. `vosk-model-small-en-us-0.15`)
- Ollama installed with a compatible local model
- Piper voice installed (e.g. `en_US-amy-low`)
- SoX (`sudo apt install sox`)

---

## ⚙️ How It Works

1. `audio_loop.py` captures mic input and queues chunks.
2. `processing_loop()` runs:
   - ✅ Vosk STT (millisecond latency)
   - 🤖 LLM inference via Ollama
   - 🗣️ Piper TTS, post-processed with SoX
   - 🔊 Plays audio response
3. Full turn logs with timings (STT, inference, TTS, FX, playback)

---

## 🧪 Sample Log

```txt
User: hello storm trooper
[Timing] Inference: 1160 ms
Stormtrooper: Silence is a weapon, and obedience is strength.
[Timing] Piper inference: 2011 ms
[Timing] Playback: 2895 ms
