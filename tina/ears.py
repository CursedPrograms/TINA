"""
ears.py - TINA hears you: Whisper (faster-whisper, on the CPU so it never
fights Ollama or ComfyUI for the GPU) and her wake word, "Hey TINA".

The page listens and sends each thing you say as a short clip (POST /hear).
While she waits for her name, a clip only counts if it has it: "Hey TINA,
check on NORA" is a task straight away, "Hey TINA" on its own gets a "Yes?"
and she listens for the task.
"""

import os
import re
import tempfile
import threading

# Whisper hears her name a few ways.
NAME = r"(?:tina|teena|tena|deena|tiina|tinna)"
WAKE = re.compile(rf"^\W*(?:(?:hey|hi|hello|ok|okay|yo)\W+)?{NAME}\b\W*", re.I)
WAKE_ANYWHERE = re.compile(rf"\b(?:hey|hi|ok|okay)\W+{NAME}\b\W*", re.I)
NOISE = {"", "you", "thank you.", "thanks for watching!", "bye."}     # what Whisper makes of silence
# The fleet's names, so Whisper writes "KIDA01" and "NORA", not "Kita zero one" and "Nora"
# (Piper-spoken test, base.en: 4 of 5 names right with it, 1 without; small.en was no better, at twice the time).
NAMES = "TINA, NORA, MILA, KIDA00, KIDA01, NINA, WHIP, IDA, ARM, DREAM, RIFT."


class Ears:
    def __init__(self, cfg):
        self.model_name = cfg["Whisper"]
        self._model = None
        self._lock = threading.Lock()
        self.error = None

    def status(self):
        return {"model": self.model_name, "loaded": self._model is not None, "error": self.error}

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, data, suffix=".webm"):
        """Audio bytes (whatever the browser recorded) -> what was said."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            with self._lock:
                segments, _ = self._load().transcribe(path, language="en", beam_size=1, vad_filter=True,
                                                      initial_prompt=NAMES)
                text = " ".join(s.text.strip() for s in segments).strip()
            if NAMES.rstrip(".") in text:       # near-silence can echo the hint back
                text = ""
            self.error = None
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            raise
        finally:
            os.unlink(path)
        return "" if text.lower() in NOISE else text


def wake(text):
    """(woke?, the rest): did they say her name, and what came after it."""
    m = WAKE.search(text) or WAKE_ANYWHERE.search(text)
    if not m:
        return False, ""
    return True, text[m.end():].strip(" ,.!?")
