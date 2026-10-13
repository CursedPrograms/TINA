"""TINA's settings: config.json next to tina.py (edit with options.bat)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {
    "Port": 5013,
    "Model": "qwen3:4b",              # NOTE(move): meant for a bigger model once TINA moves to the other PC
    "OllamaUrl": "http://localhost:11434",
    "Think": False,                   # qwen3's thinking mode plans better but is far slower here (NOTE(move): turn on there)
    "ContextTokens": 8192,
    "MaxSteps": 25,
    "Roots": [str(ROOT.parent)],      # folders she may read and work in (the GitHub folder by default)
    "AutoEdit": True,                 # edits inside Roots go ahead (logged; git can undo them); False = ask each time
    "HindsightUrl": "http://localhost:8888",   # DREAM hosts it. NOTE(move): once TINA moves, point this at DREAM's PC
    "MemoryBank": "tina",
    "RiftHost": "127.0.0.1",
    "RiftPort": 5000,
    "NinaUrl": "http://127.0.0.1:5012",
    "VoicePitch": 1.15,               # her Brainfuck beeps, relative to the phrasebook's tones
    "Voice": "en_GB-jenny_dioco-medium",   # her Piper voice (voices/<name>.onnx; run.bat fetches it)
    "Whisper": "base.en",             # her ears (faster-whisper on the CPU; tiny.en is quicker, small.en hears better)
    "MusicFolder": "../NORA-Robot-v00/music",   # the fleet's songs, numbered as on NORA's SD card (relative to TINA)
    "MusicLatency": 0.0,              # seconds her speakers lag, taken off so she starts in time with the others
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(ROOT / "config.json", encoding="utf-8") as f:
            cfg.update(json.load(f)["Config"]["TINA"])
    except (OSError, ValueError, KeyError):
        pass
    cfg["Roots"] = [str((ROOT / r).resolve()) for r in cfg["Roots"]]   # relative = relative to TINA's folder
    return cfg
