"""ears.py: the fleet's names as Whisper's hint, and silence giving "" (never the hint echoed back)."""
import io
import os
import wave
from types import SimpleNamespace

import pytest

from tina import ears as E


def silence_wav(seconds=2.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


class FakeModel:
    def __init__(self, text):
        self.text, self.calls = text, []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        return [SimpleNamespace(text=self.text)], None


@pytest.mark.parametrize("heard,want", [
    (" Tell KIDA01 to stop.", "Tell KIDA01 to stop."),
    (E.NAMES, ""),                                  # near-silence echoing the hint
    (" thank you.", ""),                            # what Whisper makes of silence
])
def test_hint_and_guard(heard, want):
    ears = E.Ears({"Whisper": "base.en"})
    ears._model = FakeModel(heard)
    assert ears.transcribe(silence_wav(), suffix=".wav") == want
    assert ears._model.calls[0]["initial_prompt"] == E.NAMES and ears._model.calls[0]["language"] == "en"


def test_wake():
    assert E.wake("Hey Tina, check on NORA.") == (True, "check on NORA")
    assert E.wake("hey teena") == (True, "")
    assert E.wake("what about NORA") == (False, "")


def test_real_whisper_silence():
    """The real base.en on two seconds of silence: "" (skipped if faster-whisper or the model isn't here)."""
    pytest.importorskip("faster_whisper")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")    # never download in a test
    ears = E.Ears({"Whisper": "base.en"})
    try:
        ears._load()
    except Exception as e:
        pytest.skip(f"Whisper base.en isn't available offline: {type(e).__name__}")
    assert ears.transcribe(silence_wav(), suffix=".wav") == ""
