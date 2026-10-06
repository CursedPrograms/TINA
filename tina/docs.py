"""
docs.py - "chat with your documents" (the AnythingLLM idea), built in.

Every text file under TINA's roots (READMEs, TODOs, code, sketches, configs)
is cut into overlapping chunks and indexed with BM25 - plain keyword ranking,
no embedding model to download or run. Good at "where is X", "how does Y
work", "what did the TODO say about Z". The index is saved and only files that
changed since the last search are re-read.
"""

import json
import math
import os
import re
import time
from collections import Counter
from pathlib import Path

TEXT_EXT = {".md", ".txt", ".py", ".ino", ".h", ".hpp", ".c", ".cpp", ".cs", ".js", ".html", ".css", ".json",
            ".bat", ".ps1", ".sh", ".yaml", ".yml", ".xml", ".go", ".rs", ".jl", ".kt", ".fs", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {"Wav2Lip", "musetalk", "deepface", "facefusion", "jsartoolkit5", "Roop-FaceSwap", "Deep-Dream-Generator",
             "Monocular-Depth", "Qwen-Image-2.1-Uncensored-GGUF", "qwen-undress", "third_party", "vendor", "demo_images", ".git", "venv", "venv311", ".venv", "hindsight-venv", "node_modules", "Library", "Temp", "Logs", "obj",
             "build", "__pycache__", "site-packages", "output", "models", "musetalk_out", "results", "dist", "Packages",
             "PackageCache", "checkpoints", "weights"}
MAX_FILE = 120_000          # bytes; bigger files are generated or data, not docs
MAX_TOTAL = 40_000_000      # stop indexing past this much text, so the index never eats the RAM
CHUNK_LINES, OVERLAP = 40, 10
K1, B = 1.4, 0.75
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")


def tokens(text):
    out = []
    for word in _WORD.findall(text):
        out.append(word.lower())
        parts = re.findall(r"[a-z]+|\d+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word).lower())
        if len(parts) > 1:      # snake_case / camelCase: index the pieces too
            out.extend(p for p in parts if len(p) > 1)
    return out


class DocIndex:
    def __init__(self, roots, path):
        self.roots = [Path(r) for r in roots]
        self.path = Path(path)
        self.files = {}          # rel path -> {"mtime", "chunks": [{"start", "text", "tf"}]}
        self._loaded = False

    # -- building
    def _walk(self):
        for root in self.roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
                for f in filenames:
                    p = Path(dirpath, f)
                    if p.suffix.lower() in TEXT_EXT:
                        yield p

    def _chunk(self, text):
        lines = text.splitlines()
        out = []
        for start in range(0, max(1, len(lines)), CHUNK_LINES - OVERLAP):
            part = "\n".join(lines[start:start + CHUNK_LINES])
            if part.strip():
                out.append({"start": start + 1, "text": part, "tf": dict(Counter(tokens(part)))})
            if start + CHUNK_LINES >= len(lines):
                break
        return out

    def refresh(self):
        """Re-read changed files, drop deleted ones. Returns (files, chunks)."""
        if not self._loaded:
            self._load()
        seen, total = set(), 0
        for p in self._walk():
            if total > MAX_TOTAL:
                break
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE:
                continue
            rel = str(p)
            seen.add(rel)
            total += st.st_size
            have = self.files.get(rel)
            if have and have["mtime"] == st.st_mtime:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            self.files[rel] = {"mtime": st.st_mtime, "chunks": self._chunk(text)}
        for rel in list(self.files):
            if rel not in seen:
                del self.files[rel]
        self._save()
        return len(self.files), sum(len(f["chunks"]) for f in self.files.values())

    def _load(self):
        self._loaded = True
        try:
            self.files = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.files = {}

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.files), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- searching
    def search(self, query, k=6, refresh_every_s=300):
        if not self._loaded or time.time() - getattr(self, "_refreshed", 0) > refresh_every_s:
            self.refresh()
            self._refreshed = time.time()
        q = [t for t in tokens(query)]
        if not q:
            return []
        chunks = [(rel, c) for rel, f in self.files.items() for c in f["chunks"]]
        n = len(chunks) or 1
        avg = sum(sum(c["tf"].values()) for _, c in chunks) / n
        df = Counter()
        for _, c in chunks:
            for t in set(q) & c["tf"].keys():
                df[t] += 1
        scored = []
        for rel, c in chunks:
            length = sum(c["tf"].values())
            s = 0.0
            for t in set(q):
                f = c["tf"].get(t)
                if not f:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * length / avg))
            if s > 0:
                name = rel.lower()
                if any(t in name for t in q):          # the file's name matches: likely the one
                    s *= 1.3
                scored.append((s, rel, c))
        scored.sort(key=lambda x: -x[0])
        return [{"file": rel, "line": c["start"], "score": round(s, 2), "text": c["text"]} for s, rel, c in scored[:k]]
