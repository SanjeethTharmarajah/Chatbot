# SANJAYBOT — Windows 11 Offline 3D Voice Assistant

This is the next-stage Windows 11 project: a desktop GUI with a procedural 3D robot,
continuous voice activity detection, local Whisper transcription, Ollama chat, Kokoro
neural speech, and an audio-reactive mouth/jaw.

## 1. Install Ollama

Download and install Ollama for Windows:
https://ollama.com/download/windows

Open PowerShell and run:

```powershell
ollama pull llama3.2:3b
```

Ollama normally runs as a background Windows app/service. Check it with:

```powershell
ollama list
```

If you prefer a smaller/faster model, you can use `llama3.2:1b` and change
`OLLAMA_MODEL` near the top of `app.py`.

## 2. Install Python

Install 64-bit Python 3.11 or 3.12 from:
https://www.python.org/downloads/windows/

During installation, enable **Add python.exe to PATH**.

## 3. Create the environment

Extract this ZIP, open PowerShell in the extracted `SANJAYBOT_Windows_3D` folder,
then run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

If PowerShell blocks activation, run this once in that PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 4. Start SANJAYBOT

```powershell
python app.py
```

First run downloads Whisper and Kokoro model files. After those files and the Ollama
model are installed, transcription, chat inference and speech synthesis run locally.
Keep Ollama running in the background.

## 5. Settings

Edit the constants near the top of `app.py`:

- `OLLAMA_MODEL`: model already downloaded in Ollama.
- `WHISPER_MODEL`: `tiny.en` for speed, `base.en` balanced, `small.en` for accuracy.
- `WHISPER_DEVICE`: `cpu`, or `cuda` if you have installed a compatible CUDA-enabled
  CTranslate2/PyTorch environment and verified it works.
- `TTS_VOICE`: `am_michael`, `af_heart`, `af_bella`, etc. Kokoro voice availability
  depends on the installed Kokoro version.

## Voice commands

- "stop speaking"
- "stop listening"
- "start listening"
- "clear chat"
- "goodbye"

The assistant automatically resumes listening after each answer unless you pause it.

## Important notes / limitations

- The 3D robot is procedurally generated with OpenGL primitives. It is a 3D animated
  metallic robot, but not a photorealistic scanned/rigged character model.
- Lip movement follows the generated audio's amplitude envelope. It is audio-reactive
  mouth/jaw animation, not phoneme-viseme-grade facial mocap.
- Microphone capture uses WebRTC VAD. In a noisy room, change `self.vad = webrtcvad.Vad(2)`
  to aggressiveness 3; if it misses quiet speech, try 1.
- The application continuously listens while listening mode is enabled. The microphone
  audio is processed locally and is not uploaded by this app.
- Ollama and model setup require internet once; after downloads, normal inference is local.
- Some Kokoro dependencies may take a few minutes to install on Windows. If pip reports
  a PyTorch-related issue, install the matching PyTorch build first from
  https://pytorch.org/get-started/locally/ and then retry `pip install kokoro`.

## Troubleshooting

### Ollama connection error
Open Ollama from the Start menu, then check `http://127.0.0.1:11434` or run
`ollama list` in PowerShell.

### No microphone audio
Windows Settings → Privacy & security → Microphone → enable microphone access for
desktop apps. Also check Settings → System → Sound → Input.

### Slow responses
Use `tiny.en` Whisper and `llama3.2:1b`; a GPU-capable setup can improve performance.
