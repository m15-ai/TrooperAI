# TrooperAI: Local Real-Time Voice Assistant

Trooper is a low-latency, local-first voice assistant implemented in Python. It combines real-time speech recognition, LLM-based dialog, and high-quality TTS into a reactive system running on Raspberry Pi5 (or other Linux systems).

<insert photo> 

The device ...

<insert video>

Check it out in action.

------

## Features

- Fully integrated into headless Raspberry Pi5
- Full-duplex mic/speaker support
- LED mode feedback (listening / speaking / thinking)
- Sentence streaming STT using lightweight Vosk model
- Sentence-by-sentence streaming TTS using Piper
- Mic-mute mode for setup with a speaker and separate mic
- Interruptible architecture (when mic mute is disabled)
- Configurable device names (mic and speaker)
- JSON-based configuration: `.trooper_config.json`
- Graceful handling of missing audio devices
- Arcade style lighted button for visual feedback and control
- Realistic Trooper voice using stock Piper voice
- Client triggered via gesture or button press

## Core Architecture

The primary goal of the project to was to create a local voice solution, small enough to install in a life-size storm trooper, with acceptable latency to allow for a natural conversation with the Trooper.

#### Speech Input

The system captures audio through a Playstation PS-Eye mic arrary connected to the Raspberry Pi5 via USB-A port. The PS-Eye has a 4 mic array that is sensitive enough to allow users at a distance to be able to speak to the Trooper.

Audio-in highlights:

- Uses `PyAudio` to capture live mic input.
- Optional voice activity detection (VAD) gates LED feedback.
- Audio is streamed to the server in 16kHz mono PCM format.

#### Speech Recognition (STT)

- Vosk is used in batch mode.
- Each utterance is sent to the LLM only after a silence break.

#### LLM

The system uses local install of Ollama to provide API for the chosen LLM model. Two models have been tested expensively with the project:

```
Ollama list:

111

222
```

Choose your model:

```
  "model_name": "gemma3:1b",
```

The system implements configurable System Prompt to give the Trooper his personality. The default  System Prompt for Trooper is stored in the JSON configuration file:

```
"system_prompt": "You are a loyal Imperial Stormtrooper. You need to keep order. Your weapon is a gun. Dont ask to help or assist.",
```

Blah

- Uses `Ollama` to stream JSON token-by-token responses.
- Each sentence-ending token triggers real-time TTS.
- Multiple models supported using Ollama

#### Text-to-Speech (TTS)

- Piper generates 16kHz mono audio.
- SoX upsamples to 48kHz stereo.
- Optional Retro Voice FX filtering (SoX high-pass, low-pass, compand, and noise mix) can be applied using SoX high-pass, low-pass, and noise effects.
- Audio is streamed back to the client in ~2048 byte chunks.

#### Audio Output

Audio is implemented using a low-cost USB speaker.

- Audio is played in a background thread using `PyAudio`.
- ~50ms of silence is prepended to each sentence to avoid clipping.
- Optional fade-in/fade-out applied (see config).
- A playback queue ensures smooth streaming.
- Fade-in and Fade-out effects are applied to each complete Trooper voice output to minimize defects.

#### LED/Switch/Camera Integration

The system integrates an LED / Switch combination. The LED is used to communicate status of the system. The part is the AdaFruit 30mm illuminated arcade style button. The build in switch can be used to start/stop a session with the Trooper.

- LED modes reflect states: `listen`, `blink`, `speak`, `solid`.
- Controlled via FIFO pipe (`/tmp/trooper_led`) and interpreted by `main.py`.

The switch is wired into GPIO pins of the Raspberry Pi5.

<insert pic of the wiring diagram>

Microphone PS2 Eye device.

------

## Project Structure

```
Trooper/
├── client.py             # Audio I/O, mic, speaker, LED
├── server.py             # LLM, STT, TTS processing
├── main.py               # Launches client on gesture/button
├── utils.py              # Shared helpers (e.g. led_request)
├── voices/               # Piper voice models
├── .trooper_config.json  # JSON config file
├── requirements.txt      # Dependencies file
├── client.log            # Log output for client debug
```

------

## Project Requirements

------

### Python Dependencies

Install all required Python packages via:

```bash
pip install -r requirements.txt
```

**`requirements.txt`**

```txt
aiofiles==23.2.1
aiohttp==3.9.3
asyncio
numpy==1.26.4
pyaudio==0.2.13
python-dotenv==1.0.1
soxr==0.3.7
soundfile==0.12.1
websockets==12.0
vosk==0.3.45
gpiozero==2.0
lgpio==0.0.4
opencv-python==4.9.0.80
mediapipe==0.10.9
```

> ⚠️ `pyaudio` may require `portaudio19-dev` to build correctly on some systems.

------

### System Dependencies

These are **not** installed via pip and must be installed via your OS package manager or manually.

#### APT Install (Debian / Ubuntu)

```bash
sudo apt update && sudo apt install -y \
    sox \
    pulseaudio \
    ffmpeg \
    python3-pyaudio \
    libasound-dev \
    portaudio19-dev
```

#### Piper (Text-to-Speech Engine)

Used for fast local speech synthesis.

```bash
# Build from source (requires Rust)
cargo install piper

# OR download a prebuilt binary from:
# https://github.com/rhasspy/piper/releases
```

> Place the binary at `~/.local/bin/piper` or update the path in `server.py`.

#### Ollama (LLM Backend)

Ollama runs your local language models like `gemma` or `llama3`.

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Start and load your preferred model:

```bash
ollama serve &
ollama pull gemma:2b
```

#### Audio System

Ensure `PulseAudio` is running:

```bash
pulseaudio --start
```

Make sure your user is in the audio group:

```bash
sudo usermod -aG audio $USER
```

Then log out or reboot.

## Configuration

The system is configured via a JSON file named `.trooper_config.json`, located in the home directory. This file controls audio devices, behavior, personality, and more.

#### USB-Based Configuration Override

To support headless operation, configuration updates can be applied via a USB flash drive:

- Format the drive with the name: `Trooper`
- Place a file named: `trooper_config.json` in the root of the USB
- On boot or restart, if the USB file is detected, it will:
  - Be **loaded immediately**
  - Be **copied** to `~/.trooper_config.json`, making it the new default

This allows users to easily update the Trooper's persona (e.g. voice, model, prompt) without SSH access.

------

#### Sample Configuration

```
{
  "mic_name": "USB Camera-B4.09.24.1: Audio",
  "audio_output_device": "USB PnP Sound Device: Audio",
  "mute_mic_during_playback": true,
  "fade_duration_ms": 10,
  "retro_voice_fx": false,
  "history_length": 6,
  "model_name": "llama3:8b",
  "voice": "danny-low.onnx",
  "volume": 90,
  "system_prompt": "You are a loyal Imperial Stormtrooper. Keep responses terse and authoritative."
}
```

------

#### Parameter Descriptions

| Key                        | Description                                                  |
| -------------------------- | ------------------------------------------------------------ |
| `mic_name`                 | Match string for input device name                           |
| `audio_output_device`      | Match string for output device name                          |
| `mute_mic_during_playback` | Prevents mic bleed during playback (recommended `true`)      |
| `fade_duration_ms`         | Fade duration at start/end of playback. Use `0` to disable.  |
| `retro_voice_fx`           | If `true`, applies SoX-based filtering to make audio sound more vintage |
| `history_length`           | Number of past exchanges to include in LLM context           |
| `model_name`               | Name of the local model to use via Ollama (e.g. `gemma:2b`, `llama3:8b`) |
| `voice`                    | Filename of the Piper voice model in the `voices/` directory |
| `volume`                   | System volume level (0–100) applied at startup               |
| `system_prompt`            | The LLM's default persona instruction (e.g. for tone, role, behavior) |

## Vision-Based Wake (Gesture Detection)

TrooperAI supports **gesture-based activation** as an alternative to the physical button.

Using a webcam and the MediaPipe library, the system continuously monitors for a raised open hand gesture using real-time hand landmark detection. When five fingers are detected extended for a brief streak, Trooper toggles its session (start/stop).

#### Activation Logic

- Uses **MediaPipe Hands** for landmark tracking
- Requires 5 fingers to be up
- Requires a **streak** of consistent detection (e.g. 5 frames in a row)
- Cooldown enforced between gesture activations (default: 10 seconds)

#### Requirements

This feature requires:

- `opencv-python`
- `mediapipe`

These are included in the `requirements.txt`.

#### Enable Gesture Detection

Gesture detection is optional and controlled via config:

```
jsonCopyEdit{
  "vision_wake": true
}
```

Set this flag in your `.trooper_config.json` or `trooper_config.json` on the USB.

## Systemd Integration

TrooperAI is designed to run automatically at boot using systemd:

### Services:

- `trooper-server.service`: runs the LLM + TTS backend (`server.py`)
- `trooper-main.service`: launches the LED/session manager (`main.py`)

### Example: `/etc/systemd/system/trooper-server.service`

```
[Unit]
Description=Trooper Voice Server (LLM + TTS)
After=network.target sound.target

[Service]
ExecStart=/usr/bin/python3 /home/mjw/Trooper/server.py
WorkingDirectory=/home/mjw/Trooper
Restart=always
User=mjw

[Install]
WantedBy=multi-user.target
```

### Example: `/etc/systemd/system/trooper-main.service`

```
[Unit]
Description=Trooper Main Controller (LED + Session Launcher)
After=trooper-server.service

[Service]
ExecStart=/usr/bin/python3 /home/mjw/Trooper/main.py
WorkingDirectory=/home/mjw/Trooper
Restart=always
User=mjw

[Install]
WantedBy=multi-user.target
```

### Setup:

```
sudo systemctl enable trooper-server.service
sudo systemctl enable trooper-main.service
sudo systemctl start trooper-server.service
sudo systemctl start trooper-main.service
```

To verify:

```
systemctl status trooper-server
systemctl status trooper-main
```

Use `systemctl list-unit-files | grep trooper` to confirm they are enabled.

## Debugging & Logs

- `client.py` logs to `/tmp/client.log` via `subprocess.Popen()`
- LED output visible by tailing FIFO `/tmp/trooper_led`
- Use `list_pyaudio_devices()` to confirm audio device names

------

## Robustness

- Audio device check with fallback to default
- LED state debounce for VAD `listen` mode
- Sentence filter avoids empty or punctuation-only TTS output
- Graceful WebSocket disconnect handling

------

## Performance

- STT ~10ms
- LLM ~3–15 sec depending on prompt
- TTS ~2–5 sec per response
- All speech streamed sentence-by-sentence for responsiveness

#### CPU Usage

Blah

<insert inage>

More

------

## Requirements

Requirements.txt

```
# Python dependencies for Trooper project

vosk>=0.3.45
PyAudio>=0.2.14
requests>=2.32.3

# System / CLI dependencies (install via your OS package manager)
# - sox       (for audio mixing and filters)
# - ffmpeg    (for recording/encoding audio)
# - piper     (for TTS voice model)
# - pulseaudio-utils or pipewire-pulse (for capturing system audio via PulseAudio)
# - ollama    (Ollama CLI for local LLM inference)
```

blah

- Python 3.11+
- PyAudio
- Vosk
- Piper
- SoX
- websockets
- numpy

blah

## Future Improvements

- Whisper streaming for STT
- Full-duplex interrupt handling
- Dynamic context summarization
- Web dashboard or status indicator
- USB mic reconnect support / device hotplug

## License

blah

## Get Involved

This is a live, evolving system. Feature contributions and performance ideas are welcome!