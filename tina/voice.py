"""
voice.py - TINA's voice: Piper for words, the PC's speakers for her Brainfuck.

Every line is rendered once and cached in voice_cache/ (mp3 when ffmpeg is
around, wav otherwise), so "I'm working, don't interrupt me" doesn't run Piper
each time. Her Brainfuck is the fleet's phrasebook (RIFT/Fleet/brainfuck_talk.py)
beeped one tone per symbol, pitched to her voice, rendered to a wav and played
through the sound card like everything else.

The page plays her spoken replies itself (GET /voice/<file>); the busy notice,
her chirps and her peer conversations play here, on the PC.
"""

import array
import hashlib
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from .config import ROOT

CACHE = ROOT / "voice_cache"
RATE = 22050
FADE_MS = 3            # soft edges on each beep, so the symbols don't click


def _find_piper():
    """Her own venv's Piper, else DREAM's (DREAM installs it too)."""
    for p in (ROOT / "venv" / "Scripts" / "piper.exe", ROOT / "venv" / "bin" / "piper",
              ROOT.parent / "DREAM" / "venv311" / "Scripts" / "piper.exe"):
        if p.exists():
            return str(p)
    return shutil.which("piper")


def phrasebook():
    """RIFT's brainfuck_talk module (the fleet's phrasebook), or None if RIFT isn't next to her."""
    fleet = str(ROOT.parent / "RIFT" / "Fleet")
    if fleet not in sys.path:
        sys.path.insert(0, fleet)
    try:
        import brainfuck_talk
        return brainfuck_talk
    except ImportError:
        return None


class Voice:
    def __init__(self, cfg):
        self.model = ROOT / "voices" / f"{cfg['Voice']}.onnx"
        self.pitch = cfg["VoicePitch"]
        self.piper = _find_piper()
        self.ffmpeg = shutil.which("ffmpeg")
        self._lock = threading.Lock()      # one sound at a time
        self._mixer = None                 # pygame.mixer once it's up, False if there's no audio device
        self._utts = None

    def status(self):
        return {"piper": bool(self.piper), "voice": self.model.exists(), "brainfuck": phrasebook() is not None}

    # ---------------------------------------------------------------- rendering
    def render(self, text):
        """Her line as an audio file in the cache (rendered the first time). None if Piper can't."""
        text = " ".join((text or "").split())
        if not text or not self.piper or not self.model.exists():
            return None
        key = hashlib.sha1(f"{self.model.name}|{text}".encode("utf-8")).hexdigest()[:16]
        for ext in (".mp3", ".wav"):
            if (CACHE / (key + ext)).exists():
                return CACHE / (key + ext)
        CACHE.mkdir(exist_ok=True)
        wav = CACHE / (key + ".wav")
        try:
            r = subprocess.run([self.piper, "-m", str(self.model), "-f", str(wav)], input=text.encode("utf-8"),
                               capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            print(f"[TINA] Piper failed: {e}")
            return None
        if r.returncode != 0 or not wav.exists() or wav.stat().st_size < 100:
            print(f"[TINA] Piper failed: {r.stderr.decode('utf-8', 'ignore')[-300:]}")
            wav.unlink(missing_ok=True)
            return None
        if self.ffmpeg:
            mp3 = CACHE / (key + ".mp3")
            done = subprocess.run([self.ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-q:a", "4", str(mp3)],
                                  capture_output=True, timeout=60).returncode == 0
            if done and mp3.exists():
                wav.unlink(missing_ok=True)
                return mp3
        return wav

    def code(self, u):
        """The Brainfuck program for phrasebook utterance u (0-16)."""
        if self._utts is None:
            bf = phrasebook()
            self._utts = {i: c for i, _, _, _, c in bf.utterances()} if bf else {}
        return self._utts.get(u)

    def render_beeps(self, code):
        """A Brainfuck program as her beeps: one tone per symbol, the phrasebook's tones and lengths."""
        bf = phrasebook()
        if not bf or not code:
            return None
        key = hashlib.sha1(f"bf|{self.pitch}|{code}".encode()).hexdigest()[:16]
        path = CACHE / f"bf_{key}.wav"
        if path.exists():
            return path
        CACHE.mkdir(exist_ok=True)
        samples = array.array("h")
        fade = int(RATE * FADE_MS / 1000)
        for c in code:
            n = int(RATE * bf.LENGTHS.get(c, bf.DEFAULT_MS) / 1000)
            f = bf.TONES.get(c, 1000) * self.pitch
            for i in range(n):
                edge = min(1.0, i / fade, (n - i) / fade)
                samples.append(int(9000 * edge * math.sin(2 * math.pi * f * i / RATE)))
            samples.extend([0] * int(RATE * bf.GAP_MS / 1000))
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(samples.tobytes())
        return path

    # ---------------------------------------------------------------- playing (the PC's speakers)
    def _ready(self):
        if self._mixer is None:
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            try:
                import pygame
                pygame.mixer.init()
                self._mixer = pygame.mixer
            except Exception as e:
                print(f"[TINA] No audio output: {e}")
                self._mixer = False
        return self._mixer

    def play(self, path, wait=True):
        if not path:
            return False
        if not wait:
            threading.Thread(target=self.play, args=(path, True), daemon=True).start()
            return True
        with self._lock:
            mixer = self._ready()
            if not mixer:
                return False
            try:
                mixer.music.load(str(path))
                mixer.music.play()
                while mixer.music.get_busy():
                    time.sleep(0.05)
                mixer.music.unload()
            except Exception as e:
                print(f"[TINA] Couldn't play {path}: {e}")
                return False
        return True

    def say(self, text, wait=True):
        """Say text aloud on the PC (Piper)."""
        return self.play(self.render(text), wait)

    def beep(self, code, wait=False):
        """Beep a Brainfuck program on the PC."""
        return self.play(self.render_beeps(code), wait)


VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def fetch(name):
    """Download Piper voice name (e.g. en_GB-jenny_dioco-medium) into voices/ unless it's there."""
    import requests
    lang, speaker, quality = name.split("-")
    for ext in (".onnx", ".onnx.json"):
        path = ROOT / "voices" / (name + ext)
        if path.exists():
            continue
        path.parent.mkdir(exist_ok=True)
        print(f"Fetching her voice: {path.name}")
        r = requests.get(f"{VOICES_URL}/{lang.split('_')[0]}/{lang}/{speaker}/{quality}/{name}{ext}", timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)


if __name__ == "__main__":     # run.bat: python -m tina.voice
    from .config import load
    fetch(load()["Voice"])
