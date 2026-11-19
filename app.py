"""
Floating Alien Voice Assistant for Windows 10/11.
Transparent, frameless, always-on-top desktop avatar; click to pause/resume listening,
right-click for controls. Uses local Whisper, Ollama and Kokoro.
"""
import os
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

import sys, time, queue, threading, re, webbrowser, urllib.parse
from datetime import datetime
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import webrtcvad
import pyautogui

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPoint
from PySide6.QtGui import QPainter, QPixmap, QColor, QPen, QBrush, QRadialGradient
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QMenu
import librosa
AVATAR_IMAGE = Path(__file__).with_name("babyallien-removebg-preview.png")

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OLLAMA_MODEL = "llama3.2:3b"
WHISPER_MODEL = "base.en"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE = "int8"
MIC_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = MIC_RATE * FRAME_MS // 1000
SILENCE_END_MS = 900
MIN_SPEECH_MS = 300
MAX_UTTERANCE_SECONDS = 18
TTS_RATE = 24000
TTS_VOICE = "af_sky"
TTS_SPEED = 1.12
SYSTEM_PROMPT = (
    "You are SANJAYBOT, a helpful, friendly desktop AI assistant. "
    "Speak naturally and conversationally. Keep spoken answers concise unless "
    "the user asks for detail. Never claim to have performed an action unless "
    "this program actually did it."
)

class Bus(QObject):
    status = Signal(str)
    user = Signal(str)
    assistant = Signal(str)
    error = Signal(str)
    audio_level = Signal(float)
    listening = Signal(bool)
    speaking = Signal(bool)
    ready = Signal()

class FloatingAlien(QWidget):
    """Transparent image-only desktop avatar with audio-reactive mouth."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(245, 250)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setMouseTracking(True)
        self.pixmap = QPixmap(str(AVATAR_IMAGE))
        self.level = 0.0
        self.target_level = 0.0
        self.talking = False
        self.listening = False
        self.blink = 0.0
        self._press_pos = None
        self._dragging = False
        self.anim = QTimer(self)
        self.anim.timeout.connect(self.animate)
        self.anim.start(25)
        if self.pixmap.isNull():
            raise FileNotFoundError(
                f"Alien image not found: {AVATAR_IMAGE}. Keep the PNG beside app.py."
            )

    def set_state(self, listening=None, talking=None):
        if listening is not None:
            self.listening = listening
        if talking is not None:
            self.talking = talking
            if not talking:
                self.target_level = 0.0
        self.update()

    def set_level(self, value):
        self.target_level = float(np.clip(value, 0.0, 1.0))

    def animate(self):
        self.level += (self.target_level - self.level) * 0.48
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        fitted = self.pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        x = (self.width() - fitted.width()) / 2
        y = (self.height() - fitted.height()) / 2
        p.drawPixmap(int(x), int(y), fitted)

        # Mouth overlay sits over the alien's original small smile.
        # It opens in sync with the actual Kokoro playback audio level.
        w, h = fitted.width(), fitted.height()
        cx, cy = x + w * 0.508, y + h * 0.480
        mouth_w = w * 0.12
        openness = 3.0 + self.level * h * 0.14

        if self.talking and self.level > 0.015:
            p.setPen(QPen(QColor(27, 25, 38, 225), max(1.0, w * 0.004)))
            p.setBrush(QColor(34, 13, 26, 245))
            p.drawEllipse(int(cx-mouth_w/2), int(cy-openness/2),
                          int(mouth_w), max(2, int(openness)))
            if self.level > 0.22:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(155, 67, 86, 210))
                p.drawEllipse(int(cx-mouth_w*0.22), int(cy+openness*0.12),
                              max(2, int(mouth_w*0.44)), max(2, int(openness*0.22)))

        # A faint cyan halo indicates active listening without adding a window.
        if self.listening:
            p.setPen(QPen(QColor(65, 225, 255, 115), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(4, 4, self.width()-8, self.height()-8)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.window().show_context_menu(event.globalPosition().toPoint())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_pos
            if delta.manhattanLength() > 5:
                self._dragging = True
                self.window().move(self.window().pos() + delta)
                self._press_pos = event.globalPosition().toPoint()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._dragging:
                self.window().toggle_listening()
            self._press_pos = None
            self._dragging = False
            event.accept()

class ContinuousListener(threading.Thread):
    """Collect 20 ms microphone frames and detect completed speech with WebRTC VAD."""
    def __init__(self, on_utterance, on_level, stop_event):
        super().__init__(daemon=True)
        self.on_utterance, self.on_level, self.stop_event = on_utterance, on_level, stop_event
        self.vad = webrtcvad.Vad(2)
        self.frames = queue.Queue(maxsize=100)

    def run(self):
        def callback(indata, frames, time_info, status):
            pcm = np.clip(indata[:, 0] * 32767, -32768, 32767).astype(np.int16)
            try: self.frames.put_nowait(pcm.tobytes())
            except queue.Full: pass
        try:
            with sd.InputStream(samplerate=MIC_RATE, channels=1, dtype="float32",
                                blocksize=FRAME_SAMPLES, callback=callback):
                self._consume()
        except Exception as e:
            self.on_utterance(None, str(e))

    def _consume(self):
        active, speech_frames, silence_frames = False, [], 0
        max_frames = int(MAX_UTTERANCE_SECONDS * 1000 / FRAME_MS)
        silence_limit, min_frames = SILENCE_END_MS // FRAME_MS, MIN_SPEECH_MS // FRAME_MS
        while not self.stop_event.is_set():
            try: frame = self.frames.get(timeout=0.2)
            except queue.Empty: continue
            audio = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768
            rms = float(np.sqrt(np.mean(audio*audio) + 1e-10))
            self.on_level(min(1.0, rms*8))
            voiced = self.vad.is_speech(frame, MIC_RATE)
            if voiced:
                active = True
                speech_frames.append(frame)
                silence_frames = 0
            elif active:
                speech_frames.append(frame)
                silence_frames += 1
            if active and (silence_frames >= silence_limit or len(speech_frames) >= max_frames):
                if len(speech_frames) >= min_frames:
                    pcm = b"".join(speech_frames)
                    self.on_utterance(np.frombuffer(pcm, dtype=np.int16).astype(np.float32)/32768, None)
                active, speech_frames, silence_frames = False, [], 0

class Assistant(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SANJAYBOT Floating Alien")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.robot = FloatingAlien()
        self.setCentralWidget(self.robot)
        self.setFixedSize(self.robot.size())

        self.bus = Bus()
        self.bus.status.connect(self.set_status)
        self.bus.user.connect(lambda s: print("YOU:", s, flush=True))
        self.bus.assistant.connect(lambda s: print("ALIEN:", s, flush=True))
        self.bus.error.connect(self.add_error)
        self.bus.audio_level.connect(self.robot.set_level)
        self.bus.listening.connect(lambda active: self.robot.set_state(listening=active))
        self.bus.speaking.connect(lambda active: self.robot.set_state(talking=active))
        self.bus.ready.connect(self.on_models_ready)

        self.whisper = None
        self.kokoro = None
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.stop_event = threading.Event()
        self.listener = None
        self.busy = threading.Event()
        self.stop_requested = False
        self.tts_stream = None

        self.set_status("Loading local speech and AI models...")
        self._place_bottom_right()
        threading.Thread(target=self.load_models, daemon=True).start()

    def _place_bottom_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 28,
                      area.bottom() - self.height() - 38)

    def show_context_menu(self, point):
        menu = QMenu()
        toggle = menu.addAction("Pause listening" if self.listener and self.listener.is_alive()
                                else "Start listening")
        stop_voice = menu.addAction("Stop speaking")
        menu.addSeparator()
        quit_action = menu.addAction("Exit assistant")
        chosen = menu.exec(point)
        if chosen == toggle:
            self.toggle_listening()
        elif chosen == stop_voice:
            self.stop_speaking()
        elif chosen == quit_action:
            self.close()

    def set_status(self, value):
        self.setToolTip(value)
        self.robot.setToolTip(value)

    def add_error(self, value):
        print("ASSISTANT ERROR:", value, flush=True)
        self.set_status("Problem: " + str(value))

    def on_models_ready(self):
        self.set_status("Ready — listening. Left-click alien to pause/resume; right-click for menu.")
        self.start_listener()

    def load_models(self):
        try:
            self.bus.status.emit("Loading local Whisper...")
            from faster_whisper import WhisperModel
            self.whisper = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE,
                                        compute_type=WHISPER_COMPUTE)
            self.bus.status.emit("Loading Kokoro voice...")
            from kokoro import KPipeline
            self.kokoro = KPipeline(lang_code="a")
            self.bus.status.emit("Checking local Ollama...")
            r = requests.get("http://127.0.0.1:11434/api/tags", timeout=5)
            r.raise_for_status()
            available = [m.get("name", "") for m in r.json().get("models", [])]
            if not any(x == OLLAMA_MODEL or x.startswith(OLLAMA_MODEL + ":") for x in available):
                raise RuntimeError(f"Missing Ollama model. Run: ollama pull {OLLAMA_MODEL}")
            self.bus.ready.emit()
        except Exception as e:
            self.bus.error.emit(str(e))
            self.bus.status.emit("Setup required — right-click for Exit")

    def toggle_listening(self):
        if self.listener and self.listener.is_alive():
            self.stop_event.set()
            self.bus.listening.emit(False)
            self.bus.status.emit("Listening paused")
        else:
            self.start_listener()

    def start_listener(self):
        if self.whisper is None:
            self.bus.status.emit("Speech models are still loading")
            return
        self.stop_event.clear()
        self.listener = ContinuousListener(self.handle_utterance, self.handle_level, self.stop_event)
        self.listener.start()
        self.bus.listening.emit(True)
        self.bus.status.emit("Listening — speak naturally")

    def handle_level(self, level):
        # Listening meter is deliberately not used for mouth movement.
        pass

    def handle_utterance(self, audio, error):
        if error:
            self.bus.error.emit(error)
            return
        if audio is None or self.busy.is_set():
            return
        self.busy.set()
        threading.Thread(target=self.process_audio, args=(audio,), daemon=True).start()

    def process_audio(self, audio):
        try:
            self.bus.status.emit("Transcribing...")
            segments, _ = self.whisper.transcribe(audio, language="en", vad_filter=True)
            phrase = " ".join(s.text.strip() for s in segments).strip()
            if not phrase:
                return
            self.bus.user.emit(phrase)
            cmd = phrase.lower().strip(" .!?")
            if cmd in ("stop speaking", "stop talking", "be quiet"):
                self.stop_speaking()
                return
            if cmd in ("stop listening", "pause listening"):
                self.stop_event.set()
                self.bus.listening.emit(False)
                return
            if cmd in ("start listening", "resume listening"):
                QTimer.singleShot(0, self.start_listener)
                return
            if cmd in ("goodbye", "exit assistant", "shut down assistant"):
                QTimer.singleShot(0, self.close)
                return
            if self.handle_computer_command(phrase):
                return
            self.bus.status.emit("Thinking locally...")
            answer = self.ask_local_llm(phrase)
            self.bus.assistant.emit(answer)
            self.speak(answer)
        except Exception as e:
            self.bus.error.emit(str(e))
        finally:
            self.busy.clear()
            if not self.stop_event.is_set():
                self.bus.status.emit("Listening — speak naturally")

    def ask_local_llm(self, prompt):
        self.history.append({"role": "user", "content": prompt})
        self.history = [self.history[0]] + self.history[-12:]
        response = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL, "messages": self.history, "stream": False,
            "options": {"temperature": 0.7, "num_ctx": 4096}
        }, timeout=180)
        response.raise_for_status()
        answer = response.json()["message"]["content"].strip()
        self.history.append({"role": "assistant", "content": answer})
        return answer

    def handle_computer_command(self, phrase):
        cmd = phrase.lower().strip()

        reply = None

        # Open Windows applications
        apps = {
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "paint": "mspaint.exe",
            "file explorer": "explorer.exe",
            "task manager": "taskmgr.exe"
        }

        for name, program in apps.items():
            if cmd in (f"open {name}", f"launch {name}", f"start {name}"):
                os.startfile(program)
                reply = f"Opening {name}."
                break

        # Open websites
        if cmd.startswith("open website "):
            website = cmd.replace("open website ", "", 1).strip()
            if " " not in website:
                if not website.startswith(("http://", "https://")):
                    website = "https://" + website
                webbrowser.open(website)
                reply = "Opening website."

        if cmd == "open youtube":
            webbrowser.open("https://www.youtube.com")
            reply = "Opening YouTube."

        if cmd == "open google":
            webbrowser.open("https://www.google.com")
            reply = "Opening Google."

        # Google search
        if cmd.startswith("search google for "):
            query = phrase.strip()[18:]
            url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
            webbrowser.open(url)
            reply = "Searching Google."

        # Mouse control
        match = re.search(r"move mouse to (\d+) (\d+)", cmd)

        if match:
            x, y = map(int, match.groups())
            pyautogui.moveTo(x, y, duration=0.3)
            reply = f"Moving mouse to {x}, {y}."

        if cmd in ("click", "click mouse", "left click"):
            pyautogui.click()
            reply = "Clicking."

        if cmd in ("double click", "double click mouse"):
            pyautogui.doubleClick()
            reply = "Double clicking."

        if cmd in ("right click", "right click mouse"):
            pyautogui.rightClick()
            reply = "Right clicking."

        if cmd == "scroll down":
            pyautogui.scroll(-5)
            reply = "Scrolling down."

        if cmd == "scroll up":
            pyautogui.scroll(5)
            reply = "Scrolling up."

        # Type dictated text
        if cmd.startswith("type "):
            text_to_type = phrase.strip()[5:]
            pyautogui.write(text_to_type, interval=0.03)
            reply = "Typing."

        # Keyboard commands
        keys = {
            "press enter": "enter",
            "press space": "space",
            "press tab": "tab",
            "press escape": "esc",
            "press backspace": "backspace",
            "press delete": "delete",
            "press up": "up",
            "press down": "down",
            "press left": "left",
            "press right": "right",
            "copy": "ctrl+c",
            "paste": "ctrl+v",
            "select all": "ctrl+a",
            "undo": "ctrl+z",
            "save": "ctrl+s"
        }

        if cmd in keys:
            pyautogui.hotkey(*keys[cmd].split("+"))
            reply = "Done."

        # Volume controls
        volume_commands = {
            "increase volume": "volumeup",
            "volume up": "volumeup",
            "decrease volume": "volumedown",
            "volume down": "volumedown",
            "mute volume": "volumemute",
            "unmute volume": "volumemute"
        }

        if cmd in volume_commands:
            pyautogui.press(volume_commands[cmd])
            reply = "Volume command completed."

        # Screenshot
        if cmd in ("take screenshot", "capture screen", "screenshot"):
            folder = Path.home() / "Pictures"
            folder.mkdir(exist_ok=True)
            filename = folder / (
                "SANJAYBOT_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".png"
            )
            pyautogui.screenshot().save(str(filename))
            reply = "Screenshot saved in your Pictures folder."

        # Current time
        if cmd in ("what time is it", "tell me the time"):
            reply = "The time is " + datetime.now().strftime("%I:%M %p")

        if reply is None:
            return False

        self.bus.assistant.emit(reply)
        self.speak(reply)
        return True
   
    def speak(self, text):
        if self.kokoro is None:
            self.bus.status.emit("Kokoro voice model not ready")
            return

        try:
            self.stop_requested = False
            self.bus.speaking.emit(True)
            self.bus.status.emit("Generating speech...")

            audio_parts = []
            for _, _, audio_chunk in self.kokoro(
                text, voice=TTS_VOICE, speed=TTS_SPEED
            ):
                audio_parts.append(
                    np.asarray(audio_chunk, dtype=np.float32).reshape(-1)
                )

            if not audio_parts:
                self.bus.status.emit("No speech audio generated")
                return

            audio = np.concatenate(audio_parts)
            audio = librosa.effects.pitch_shift(
                audio.astype("float32"),
                sr=24000,
                n_steps=4
            )
            self.bus.status.emit("Speaking...")

            # Stream the generated voice and send its RMS level to the avatar.
            # The mouth therefore follows the actual Kokoro audio, not the mic.
            position = [0]

            def audio_callback(outdata, frames, time_info, status):
                if self.stop_requested:
                    outdata.fill(0)
                    raise sd.CallbackStop

                start = position[0]
                end = min(start + frames, len(audio))
                chunk = audio[start:end]

                outdata.fill(0)
                if len(chunk):
                    outdata[:len(chunk), 0] = chunk
                    rms = float(np.sqrt(np.mean(chunk * chunk) + 1e-10))
                    self.bus.audio_level.emit(min(1.0, rms * 12.0))

                position[0] = end
                if end >= len(audio):
                    raise sd.CallbackStop

            self.tts_stream = sd.OutputStream(
                samplerate=TTS_RATE,
                channels=1,
                dtype="float32",
                callback=audio_callback
            )

            with self.tts_stream:
                while self.tts_stream.active and not self.stop_requested:
                    time.sleep(0.02)

        except Exception as e:
            self.bus.error.emit("Speech error: " + str(e))

        finally:
            self.bus.audio_level.emit(0.0)
            self.tts_stream = None
            self.stop_requested = False
            self.bus.speaking.emit(False)


    def stop_speaking(self):
        self.stop_requested = True
        if self.tts_stream is not None:
            try:
                self.tts_stream.stop()
            except Exception:
                pass
        sd.stop()


    def closeEvent(self, event):
        self.stop_event.set()
        self.stop_speaking()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    win = Assistant()
    win.show()
    sys.exit(app.exec())
