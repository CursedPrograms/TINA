"""
memory.py - TINA's long-term memory, in Hindsight (bank "tina").

Hindsight stores what she's told (retain) and finds it again by meaning
(recall). DREAM hosts the server (memory_server.py in the DREAM repo) for the
whole fleet: DREAM, TINA, NINA and RIFT each keep a bank there.
fleet_memory.py (master copy in RIFT/Fleet/) does the work: HindsightUrl
"auto" finds DREAM through RIFT's fleet list, and if the server isn't
running, memories wait in memory_pending.jsonl and are sent when it's back,
so nothing is lost and no task ever fails because of memory.
"""

from .config import ROOT
from .fleet_memory import FleetMemory

BANKS = ("tina", "dream", "nina", "rift")


class Memory:
    def __init__(self, cfg):
        self.bank = cfg["MemoryBank"]
        self.fleet = FleetMemory(self.bank, url=cfg["HindsightUrl"],
                                 rift=f"http://{cfg['RiftHost']}:{cfg['RiftPort']}",
                                 pending=ROOT / "memory_pending.jsonl")

    @property
    def url(self):
        return self.fleet.url or self.fleet.wanted

    def retain(self, content, context=""):
        """Store a memory (in the background). True if the memory server is reachable right now;
        either way it's kept, and sent once the server is back."""
        self.fleet.retain(content, context)
        return self.fleet.status()["found"]

    def recall(self, query, k=6, bank=None):
        """Memories relevant to `query`, as plain sentences. [] if the server is away.
        bank: another agent's memories ("dream", "nina", "rift")."""
        return self.fleet.recall(query, k=k, bank=bank if bank in BANKS else None)

    def status(self):
        s = self.fleet.status()
        return {"url": s["url"], "bank": s["bank"], "pending": s["pending"], "reachable": s["found"]}
