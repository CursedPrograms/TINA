"""
fleet_memory.py - the fleet's long-term memory: the Hindsight server DREAM hosts.

DREAM runs the server (memory_server.py in the DREAM repo, port 8888) and
registers "memory:8888" with RIFT. Each agent keeps its own bank there:
"dream", "tina", "nina", "rift". This file is the one way in for everyone but
DREAM herself (master copy: RIFT/Fleet/; NINA and TINA carry a copy).

    mem = FleetMemory("nina", url="auto", rift="http://127.0.0.1:5000")
    mem.retain("NORA went quiet for 12 minutes", context="a fleet problem")
    mem.recall("NORA going quiet")          # -> a few sentences, or []

The rules, the same for everyone:
  * url "auto" (or an address that doesn't answer) finds DREAM through RIFT's
    /fleet: whoever registers a "memory:<port>" capability.
  * Nothing waits on it. retain() goes through a background queue; if the
    server is away the memories wait in a local file and are sent once it's
    back, so nothing is lost. recall() gives up after RECALL_TIMEOUT_S.
  * Hindsight pulls facts out of every memory with DREAM's local LLM, and DREAM
    talks with that same LLM, so chatty agents should send digests (several
    lines in one retain), not every little event.
  * No hindsight-client installed: memories just wait in the file.
"""

import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

RECALL_TIMEOUT_S = 2.0
RETRY_S = 60                 # after a failure, leave the server alone this long
FIND_EVERY_S = 60            # how often "auto" asks RIFT again where DREAM is


class FleetMemory:
    def __init__(self, bank, url="auto", rift="http://127.0.0.1:5000", pending=None, enabled=True):
        self.bank = bank
        self.wanted = (url or "auto").rstrip("/")
        self.rift = rift.rstrip("/")
        self.enabled = enabled
        self.pending = Path(pending) if pending else Path(__file__).resolve().parent / f"memory_pending_{bank}.jsonl"
        self.url = None              # where the server is, once found
        self._client, self._client_url = None, None
        self._down_until = 0.0
        self._found_at = 0.0
        self._lock = threading.Lock()
        self._jobs = queue.Queue()
        if enabled:
            threading.Thread(target=self._worker, daemon=True, name=f"memory-{bank}").start()

    # -- finding DREAM's server
    def _find(self):
        if self.wanted != "auto" and self._answers(self.wanted):
            return self.wanted
        if time.time() - self._found_at < FIND_EVERY_S and self.url:
            return self.url
        self._found_at = time.time()
        try:
            robots = requests.get(f"{self.rift}/fleet", timeout=3).json().get("robots", [])
        except (requests.RequestException, ValueError):
            robots = []
        for r in robots:
            for c in r.get("capabilities", []):
                if str(c).startswith("memory:"):
                    url = f"http://{r['ip']}:{str(c).split(':', 1)[1]}"
                    if self._answers(url):
                        return url
        if self.wanted == "auto" and self._answers("http://localhost:8888"):
            return "http://localhost:8888"         # this PC is DREAM's
        return None

    @staticmethod
    def _answers(url):
        try:
            return requests.get(f"{url}/health", timeout=2).ok
        except requests.RequestException:
            return False

    def _get(self):
        if time.time() < self._down_until:
            return None
        if not self.url or self._client_url != self.url:
            self.url = self._find()
            if not self.url:
                self._down_until = time.time() + RETRY_S
                return None
            try:
                from hindsight_client import Hindsight
            except ImportError:
                self._down_until = time.time() + 3600
                return None
            self._client, self._client_url = Hindsight(base_url=self.url, timeout=30), self.url
        return self._client

    def _failed(self):
        self._down_until = time.time() + RETRY_S
        self.url = None              # look for it again next time: DREAM may have moved

    # -- storing
    def retain(self, content, context=""):
        """Keep a memory. Never blocks: it's sent in the background, or kept in the file until it can be."""
        if not self.enabled or not content.strip():
            return
        item = {"content": content, "context": context, "ts": time.time()}
        with self._lock, open(self.pending, "a", encoding="utf-8") as f:
            f.write(json.dumps(item) + "\n")
        self._jobs.put(1)

    def _worker(self):
        self._jobs.put(1)            # anything left from last time
        while True:
            self._jobs.get()
            while not self._jobs.empty():
                self._jobs.get_nowait()
            if not self._flush():
                time.sleep(RETRY_S)
                self._jobs.put(1)

    def _flush(self):
        """Send everything waiting. True when the file is empty."""
        with self._lock:
            if not self.pending.exists():
                return True
            items = [json.loads(l) for l in self.pending.read_text(encoding="utf-8").splitlines() if l.strip()]
        sent = 0
        for it in items:
            c = self._get()
            if c is None:
                break
            try:
                c.retain(bank_id=self.bank, content=it["content"], context=it.get("context") or None,
                         timestamp=datetime.fromtimestamp(it.get("ts", time.time())), retain_async=True)
                sent += 1
            except Exception:
                self._failed()
                break
        with self._lock:                 # keep what wasn't sent, plus anything added meanwhile
            now = [l for l in self.pending.read_text(encoding="utf-8").splitlines() if l.strip()] if self.pending.exists() else []
            left = now[sent:]
            if left:
                self.pending.write_text("".join(l + "\n" for l in left), encoding="utf-8")
            elif self.pending.exists():
                self.pending.unlink()
        return not left

    # -- remembering
    def recall(self, query, k=4, bank=None):
        """What the memory knows about `query` (this agent's bank, or `bank`), as sentences. [] if it's away."""
        if not self.enabled or not query.strip():
            return []
        out = []

        def ask():
            c = self._get()
            if c is None:
                return
            try:
                res = c.recall(bank_id=bank or self.bank, query=query, max_tokens=800, budget="low")
                out.extend(str(r.text).strip() for r in (res.results or [])[:k] if getattr(r, "text", None))
            except Exception:
                self._failed()

        t = threading.Thread(target=ask, daemon=True)
        t.start()
        t.join(RECALL_TIMEOUT_S)
        return [] if t.is_alive() else out

    def status(self):
        n = sum(1 for _ in open(self.pending, encoding="utf-8")) if self.pending.exists() else 0
        return {"bank": self.bank, "url": self.url or self.wanted, "found": bool(self.url) and time.time() >= self._down_until,
                "pending": n, "enabled": self.enabled}


class Digest:
    """Collects small events and retains them as one memory every `every_s` seconds (not one LLM call each)."""

    def __init__(self, memory, context, every_s=1800, max_lines=60):
        self.memory, self.context, self.every_s, self.max_lines = memory, context, every_s, max_lines
        self._lines, self._lock = [], threading.Lock()
        threading.Thread(target=self._loop, daemon=True, name=f"digest-{memory.bank}").start()

    def add(self, line):
        with self._lock:
            self._lines.append(time.strftime("%Y-%m-%d %H:%M ") + line)
            if len(self._lines) >= self.max_lines:
                self._send()

    def _send(self):
        if self._lines:
            self.memory.retain("\n".join(self._lines), self.context)
            self._lines = []

    def _loop(self):
        while True:
            time.sleep(self.every_s)
            with self._lock:
                self._send()
