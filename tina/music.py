"""
music.py - TINA plays along when the fleet plays music together (RIFT conducts).

RIFT sends POST /music {"action": "play", "track": 100, "at": <RIFT's time>}. She
loads the song straight away, then starts it at that moment on RIFT's clock, so
she's in time with KIDA and NORA however long the message took. Her offset to
RIFT's clock comes from GET /clock (refreshed every CLOCK_S); MusicLatency in
config.json takes off her own start-up lag.

Tracks are numbered as on NORA's SD card: the number in the file name
(MusicFolder, NORA-Robot-v00/music by default: Track000.mp3, Track100.mp3...).
It plays on its own mixer channel, so her voice and beeps still get through.
"""

import re
import threading
import time
from pathlib import Path

import requests

from .config import ROOT

CLOCK_S = 60


def tracks(folder):
    """{number: file} for the mp3s in folder: Track100.mp3 -> 100."""
    out = {}
    for p in sorted(Path(folder).glob("*.mp3")):
        m = re.search(r"(\d+)", p.stem)
        if m:
            out.setdefault(int(m.group(1)), p)
    return out


class Music:
    def __init__(self, cfg, voice):
        folder = Path(cfg["MusicFolder"])
        self.folder = folder if folder.is_absolute() else (ROOT / folder).resolve()
        self.latency = cfg["MusicLatency"]
        self.rift = f"http://{cfg['RiftHost']}:{cfg['RiftPort']}"
        self.voice = voice                       # its pygame mixer is the one she plays through
        self.offset, self._synced = 0.0, 0.0     # RIFT's clock minus hers
        self.now = None                          # {"track", "state", "started"}
        self._sound = self._channel = None
        self._seq = 0                            # a newer command cancels a waiting one

    def sync(self):
        """Her offset to RIFT's clock: half the round trip either side of RIFT's answer."""
        if time.time() - self._synced < CLOCK_S:
            return self.offset
        t0 = time.time()
        rift = requests.get(f"{self.rift}/clock", timeout=2).json()["t"]
        t1 = time.time()
        self.offset, self._synced = rift - (t0 + t1) / 2, t1
        return self.offset

    def command(self, action, track=None, at=None):
        """RIFT's command: do action at RIFT-time `at`. Returns at once; the start waits on its own thread."""
        mixer = self.voice._ready()
        if not mixer:
            return {"ok": False, "error": "no audio output"}
        sound = None
        if action == "play":
            path = tracks(self.folder).get(track)
            if path is None:
                return {"ok": False, "error": f"no track {track} in {self.folder}"}
            sound = mixer.Sound(str(path))          # decode now, while there's time
        try:
            offset = self.sync()
        except (requests.RequestException, ValueError, KeyError):
            offset = self.offset                    # RIFT's clock unreachable: the last offset (0 on the same PC)
        start = (at - offset - self.latency) if at else time.time()
        self._seq += 1
        threading.Thread(target=self._at, args=(self._seq, start, action, track, sound), daemon=True).start()
        return {"ok": True, "starts_in_s": round(start - time.time(), 3), "offset_s": round(offset, 4)}

    def _at(self, seq, start, action, track, sound):
        while time.time() < start - 0.02:
            time.sleep(min(0.05, start - time.time()))
        while time.time() < start:                   # the last few ms, busy-waited
            pass
        if seq != self._seq:
            return
        mixer = self.voice._ready()
        if self._channel is None:
            mixer.set_reserved(1)
            self._channel = mixer.Channel(0)
        if action == "play":
            self._channel.stop()
            self._sound = sound
            self._channel.play(sound)
            self.now = {"track": track, "state": "playing", "started": time.time(), "late_ms": round((time.time() - start) * 1000, 1)}
        elif action == "pause":
            self._channel.pause()
        elif action == "resume":
            self._channel.unpause()
        elif action == "stop":
            self._channel.stop()
        if self.now and action != "play":
            self.now["state"] = {"pause": "paused", "resume": "playing", "stop": "stopped"}[action]

    def status(self):
        playing = self.now and self.now["state"] == "playing" and self._channel is not None and self._channel.get_busy()
        if self.now and self.now["state"] == "playing" and not playing:
            self.now["state"] = "finished"
        return {"now": self.now, "offset_s": round(self.offset, 4), "folder": str(self.folder),
                "tracks": sorted(tracks(self.folder))}
