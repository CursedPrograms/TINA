"""
memory.py - TINA's long-term memory, in Hindsight (bank "tina").

Hindsight stores what she's told (retain), finds it again by meaning (recall)
and can reason over it (reflect). DREAM hosts the server (memory_server.py in
the DREAM repo) and keeps her own bank there. If it isn't running, memories wait in a local file and are sent when
it's back, so nothing is lost and no task ever fails because of memory.

NOTE(move): when TINA moves to the other PC, DREAM keeps hosting the memory;
point HindsightUrl at DREAM's PC.
"""

import json
import threading
import time
from pathlib import Path

from .config import ROOT

PENDING = ROOT / "memory_pending.jsonl"


class Memory:
    def __init__(self, cfg):
        self.url = cfg["HindsightUrl"].rstrip("/")
        self.bank = cfg["MemoryBank"]
        self._client = None
        self._lock = threading.Lock()
        self._last_fail = 0.0

    def _get(self):
        if self._client is None:
            try:
                from hindsight_client import Hindsight
            except ImportError:
                return None
            self._client = Hindsight(base_url=self.url, timeout=20)
        return self._client

    def _up(self):
        return time.time() - self._last_fail > 60      # after a failure, don't retry for a minute

    def retain(self, content, context=""):
        """Store a memory. True if Hindsight took it; otherwise it's queued locally."""
        item = {"content": content, "context": context, "ts": time.time()}
        if self._up() and self._send(item):
            self._flush()
            return True
        with self._lock, open(PENDING, "a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        return False

    def _send(self, item):
        c = self._get()
        if c is None:
            return False
        try:
            c.retain(bank_id=self.bank, content=item["content"], context=item.get("context") or None)
            return True
        except Exception:
            self._last_fail = time.time()
            return False

    def _flush(self):
        with self._lock:
            if not PENDING.exists():
                return
            items = [json.loads(l) for l in PENDING.read_text(encoding="utf-8").splitlines() if l.strip()]
            left = [it for it in items if not self._send(it)]
            if left:
                PENDING.write_text("".join(json.dumps(it) + "\n" for it in left), encoding="utf-8")
            else:
                PENDING.unlink()

    def recall(self, query, k=6):
        """Memories relevant to `query`, as plain sentences. [] if the server is away."""
        c = self._get()
        if c is None or not self._up():
            return []
        try:
            res = c.recall(bank_id=self.bank, query=query)
        except Exception:
            self._last_fail = time.time()
            return []
        items = getattr(res, "results", None) or (res.get("results") if isinstance(res, dict) else res) or []
        out = []
        for r in items[:k]:
            text = getattr(r, "text", None) or (r.get("text") if isinstance(r, dict) else str(r))
            if text:
                out.append(str(text).strip())
        return out

    def status(self):
        pending = sum(1 for _ in open(PENDING, encoding="utf-8")) if PENDING.exists() else 0
        return {"url": self.url, "bank": self.bank, "pending": pending, "reachable": self._up() and self._get() is not None}
