"""Offline voice: Vosk speech recognition and pyttsx3 speech synthesis (optional extra)."""
from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from pathlib import Path
from typing import Callable

from .markdown import plain

SAMPLE_RATE = 16_000


class OfflineVoice:
    def __init__(self, model_path: Path):
        self.model_path = Path(model_path)

    def _listen_sync(self, max_seconds: float, stop: threading.Event | None,
                     on_partial: Callable[[str], None] | None) -> str:
        if not 1 <= max_seconds <= 30:
            raise ValueError("Aufnahmedauer muss 1 bis 30 Sekunden betragen.")
        if not (self.model_path / "conf").is_dir() and not (self.model_path / "am").is_dir():
            raise FileNotFoundError(f"Kein Vosk-Modell in {self.model_path} gefunden.")
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            raise RuntimeError("Spracheingabe benötigt 'pip install jarvis-assistant[voice]'."
                               ) from None
        SetLogLevel(-1)
        samples: queue.Queue[bytes] = queue.Queue()
        recognizer = KaldiRecognizer(Model(str(self.model_path)), SAMPLE_RATE)
        heard: list[str] = []

        def callback(indata, _frames, _time, status):
            samples.put(bytes(indata))

        end = time.monotonic() + max_seconds
        with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000, dtype="int16",
                               channels=1, callback=callback):
            while time.monotonic() < end and not (stop and stop.is_set()):
                try:
                    chunk = samples.get(timeout=0.25)
                except queue.Empty:
                    continue
                if recognizer.AcceptWaveform(chunk):
                    text = json.loads(recognizer.Result()).get("text", "").strip()
                    if text:
                        heard.append(text)
                        break  # End of the first complete utterance.
                elif on_partial:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "")
                    if partial:
                        on_partial(partial)
        if not heard:
            heard.append(json.loads(recognizer.FinalResult()).get("text", "").strip())
        return " ".join(part for part in heard if part)

    async def listen(self, seconds: float = 8, stop: threading.Event | None = None,
                     on_partial: Callable[[str], None] | None = None) -> str:
        """Record until one utterance is complete, `seconds` elapse, or `stop` is set."""
        return await asyncio.to_thread(self._listen_sync, seconds, stop, on_partial)

    @staticmethod
    async def speak(text: str) -> None:
        spoken = plain(text).strip()
        if not spoken:
            return

        def run():
            try:
                import pyttsx3
            except ImportError:
                raise RuntimeError("Sprachausgabe benötigt 'pip install jarvis-assistant[voice]'."
                                   ) from None
            engine = pyttsx3.init()
            engine.say(spoken[:4000])
            engine.runAndWait()
            engine.stop()

        await asyncio.to_thread(run)
