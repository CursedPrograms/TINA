"""
tools.py - what TINA can do, and what she has to ask first.

Few tools, each doing one clear thing: small models use a handful of plain
tools far better than many clever ones.

    free     read / list / search files, search the docs, fleet status, GET a
             robot's API, recall memories, whitelisted commands (git status,
             compile checks...), talking to a robot in Brainfuck
    logged   edit / write files inside her roots (AutoEdit; git can undo them)
    ask      any other command, git commit / push, anything that drives a robot,
             writing outside her roots
    never    deleting files: there is no tool for it
"""

import fnmatch
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import requests

OUT_LIMIT = 4000

# commands that only look, or check a build: no approval needed
SAFE_COMMANDS = [
    ("git", "status"), ("git", "diff"), ("git", "log"), ("git", "show"), ("git", "branch"), ("git", "remote"),
    ("python", "-m", "py_compile"), ("py", "-m", "py_compile"), ("node", "--check"), ("arduino-cli", "compile"),
    ("arduino-cli", "board", "list"), ("arduino-cli", "lib", "list"), ("arduino-cli", "core", "list"),
    ("dir",), ("ls",), ("where",), ("ollama", "list"), ("pytest",), ("python", "-m", "pytest"),
]
NEVER = re.compile(r"\b(format|rmdir|rd|del|erase|rm|shutdown|diskpart|reg\s+delete|mkfs|dd)\b", re.I)
DRIVE_PATHS = re.compile(r"/(fw|bw|left|right|turn|stop|mode|link|ida|motorlock|setspeed|setcal|savecal|uv|mu|chirp|board/)", re.I)


def _ago(s):
    s = int(s)
    return f"{s} s ago" if s < 90 else f"{s // 60} min ago" if s < 5400 else f"{s // 3600} h ago"


def _cut(text, limit=OUT_LIMIT):
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters cut]"


class Tools:
    def __init__(self, cfg, docs, memory, ask, talk=None):
        """ask(question) -> bool: the user's yes/no (web page buttons, or the terminal).
        talk(robot, phrase) -> str: says a phrase to a robot (web.py; she needs her speakers)."""
        self.cfg, self.docs, self.memory, self.ask, self.talk = cfg, docs, memory, ask, talk
        self.roots = [Path(r) for r in cfg["Roots"]]
        self.log = []                      # every change she made, for the report
        self._arduino = self._find_arduino_cli()

    # ---------------------------------------------------------------- helpers
    def _path(self, p):
        path = Path(p)
        if not path.is_absolute():
            path = self.roots[0] / path
        return path.resolve()

    def _inside(self, path):
        return any(path == r or r in path.parents for r in self.roots)

    @staticmethod
    def _find_arduino_cli():
        for c in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"):
            if c.exists():
                return str(c)
        return "arduino-cli"

    # ---------------------------------------------------------------- files
    def list_files(self, path=".", pattern="*"):
        base = self._path(path)
        if not base.is_dir():
            return f"Not a folder: {base}"
        items = []
        for p in sorted(base.iterdir()):
            if p.name.startswith(".") or p.name in ("venv", "venv311", "node_modules", "__pycache__", "Library"):
                continue
            if fnmatch.fnmatch(p.name, pattern):
                items.append(p.name + ("/" if p.is_dir() else f"  ({p.stat().st_size} bytes)"))
        return _cut("\n".join(items) or "(empty)")

    def read_file(self, path, start=1, lines=150):
        p = self._path(path)
        if not p.is_file():
            return f"No such file: {p}"
        text = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start))
        part = text[start - 1:start - 1 + int(lines)]
        body = "\n".join(f"{i:5}| {l}" for i, l in enumerate(part, start))
        more = (f"\n... ({len(text)} lines in all; read on with start={start + len(part)}, or search_files"
                f" this file's folder for what you need and read just around the hits)") if start - 1 + len(part) < len(text) else ""
        return _cut(body + more, 9000)

    def search_files(self, pattern, path=".", glob="*"):
        base = self._path(path)
        try:
            rx = re.compile(pattern, re.I)
        except re.error as e:
            return f"Bad pattern: {e}"
        hits = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in
                           ("venv", "venv311", ".venv", "hindsight-venv", "node_modules", "__pycache__", "Library", "build", "site-packages")]
            for f in filenames:
                if not fnmatch.fnmatch(f, glob):
                    continue
                p = Path(dirpath, f)
                try:
                    if p.stat().st_size > 400_000:
                        continue
                    for n, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{p.relative_to(base)}:{n}: {line.strip()[:160]}")
                            if len(hits) >= 60:
                                return _cut("\n".join(hits) + "\n... (stopped at 60 matches)")
                except OSError:
                    continue
        return _cut("\n".join(hits) or "No matches.")

    def edit_file(self, path, old, new):
        p = self._path(path)
        if not p.is_file():
            return f"No such file: {p}"
        with open(p, encoding="utf-8", errors="replace", newline="") as f:   # keep the file's own line endings
            text = f.read()
        if "\r\n" in text and "\r\n" not in old:
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
        count = text.count(old)
        if count == 0:
            return "The old text isn't in the file. Read the file again and copy the exact lines (spaces included)."
        if count > 1:
            return f"The old text appears {count} times. Include more surrounding lines so it matches once."
        if not self._may_write(p, f"edit {p}"):
            return "The user said no to that edit."
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(old, new))
        self.log.append(f"edited {p}")
        return f"Edited {p} ({old.count(chr(10)) + 1} lines replaced with {new.count(chr(10)) + 1})."

    def write_file(self, path, content):
        p = self._path(path)
        existed = p.exists()
        if not self._may_write(p, f"{'overwrite' if existed else 'create'} {p}"):
            return "The user said no to writing that file."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.log.append(f"{'rewrote' if existed else 'created'} {p}")
        return f"{'Rewrote' if existed else 'Created'} {p} ({len(content)} characters)."

    def _may_write(self, p, what):
        if self._inside(p) and self.cfg["AutoEdit"]:
            return True
        return self.ask(f"TINA wants to {what}. Allow?")

    # ---------------------------------------------------------------- commands
    def run_command(self, command, folder="."):
        if NEVER.search(command):
            return "Refused: TINA doesn't delete, format or shut anything down."
        cwd = self._path(folder)
        try:
            argv = shlex.split(command, posix=False)
        except ValueError as e:
            return f"Couldn't parse the command: {e}"
        if not argv:
            return "Empty command."
        low = [a.strip('"').lower() for a in argv]
        safe = any(tuple(low[:len(s)]) == s for s in SAFE_COMMANDS)
        if low[0] == "git" and len(low) > 1 and low[1] in ("commit", "push", "reset", "checkout", "rebase", "merge", "clean"):
            safe = False
        if not safe and not self.ask(f"TINA wants to run:\n  {command}\nin {cwd}. Allow?"):
            return "The user said no to running that."
        if low[0] == "arduino-cli":
            argv[0] = self._arduino
        try:
            r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=600, shell=(os.name == "nt" and low[0] in ("dir", "where")))
        except FileNotFoundError:
            return f"Not found: {argv[0]}"
        except subprocess.TimeoutExpired:
            return "Timed out after 10 minutes."
        self.log.append(f"ran `{command}` (exit {r.returncode})")
        return _cut(f"exit code {r.returncode}\n{r.stdout}{r.stderr}")

    def compile_sketch(self, folder, board="arduino:avr:uno"):
        """Arduino compile check. Boards: arduino:avr:uno, esp32:esp32:esp32, esp8266:esp8266:generic"""
        build = Path(os.environ.get("TEMP", ".")) / "tina_build" / Path(folder).name
        cmd = [self._arduino, "compile", "--fqbn", board, "--build-path", str(build), str(self._path(folder))]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"Couldn't compile: {e}"
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-25:])
        return _cut(("COMPILED OK\n" if r.returncode == 0 else "COMPILE FAILED\n") + tail)

    # ---------------------------------------------------------------- the fleet
    def fleet_status(self):
        """Now (checked live) kept apart from NINA's history, each note with its age: an old
        problem that a newer note says is fixed is not a problem now."""
        out = {"now": {}}
        try:
            robots = requests.get(f"http://{self.cfg['RiftHost']}:{self.cfg['RiftPort']}/fleet", timeout=12).json()["robots"]
            out["now"]["rift"] = "up"
            out["now"]["online"] = [{"name": r.get("name"), "url": r.get("url"), "type": r.get("type")} for r in robots]
            out["now"]["note"] = "Only these are online. A robot not listed isn't connected to RIFT right now."
        except (requests.RequestException, ValueError, KeyError):
            out["now"]["rift"] = "not answering"
        try:
            s = requests.get(f"{self.cfg['NinaUrl']}/status", timeout=4).json()
            out["now"]["nora"] = s.get("nora")
            now = time.time()
            out["nina_history"] = [f"{_ago(now - e['t'])}, {e['level']}: {e['text']} {e.get('why', '')}".strip()
                                   for e in s.get("events", [])[:8]]
            out["nina_history_note"] = "Newest first. Older notes may be resolved by newer ones; trust 'now'."
        except (requests.RequestException, ValueError, KeyError):
            out["now"]["nina"] = "not running"
        return _cut(out)

    def talk_to(self, robot, phrase="hello"):
        if self.talk is None:
            return "I can only talk to the robots from my page (python tina.py), not the terminal."
        return self.talk(robot, phrase)

    def robot_api(self, url, method="GET"):
        method = method.upper()
        if not re.match(r"https?://", url):
            return "Give the full URL, e.g. http://192.168.4.1:5002/sensors (fleet_status lists the robots)."
        if method != "GET" or DRIVE_PATHS.search(url):
            if not self.ask(f"TINA wants to send {method} {url} (this can move or change a robot). Allow?"):
                return "The user said no."
        try:
            r = requests.request(method, url, timeout=8)
            return _cut(f"{r.status_code}\n{r.text}")
        except requests.RequestException as e:
            return f"No answer: {e}"

    # ---------------------------------------------------------------- knowledge
    def search_docs(self, query):
        hits = self.docs.search(query)
        if not hits:
            return "Nothing in the docs matches."
        return _cut("\n\n".join(f"--- {h['file']} (line {h['line']})\n{h['text'][:700]}" for h in hits), 6000)

    def remember(self, fact):
        return "Remembered." if self.memory.retain(fact, context="noted by TINA") else "Memory server unavailable; noted locally."

    def recall(self, query):
        found = self.memory.recall(query)
        return "\n".join(f"- {m}" for m in found) if found else "Nothing remembered about that."

    # ---------------------------------------------------------------- the schema the model sees
    SPECS = {
        "list_files": ("List a folder (relative to the GitHub folder, or absolute).", {"path": "folder", "pattern": "glob, default *"}),
        "read_file": ("Read a file with line numbers, 150 lines at a time.", {"path": "file", "start": "first line (default 1)", "lines": "how many (default 150)"}),
        "search_files": ("Regex search through files under a folder (a robot's repo folder is best).", {"pattern": "regex", "path": "folder, e.g. MILA-Robot-v00 (default: all repos)", "glob": "file glob, e.g. *.ino or *.py (default: all files)"}),
        "edit_file": ("Replace one exact, unique piece of text in a file. Copy old text exactly from read_file (without the line numbers).", {"path": "file", "old": "exact existing text", "new": "replacement"}),
        "write_file": ("Create a new file, or replace a whole file.", {"path": "file", "content": "full contents"}),
        "run_command": ("Run a command (no shell tricks). git status/diff/log and compile checks run at once; anything else asks the user.", {"command": "the command line", "folder": "working folder (default .)"}),
        "compile_sketch": ("Compile an Arduino sketch folder to check it builds.", {"folder": "sketch folder", "board": "arduino:avr:uno | esp32:esp32:esp32 | esp8266:esp8266:generic"}),
        "fleet_status": ("Which robots are online (RIFT) and what NINA has noticed lately.", {}),
        "talk_to": ("Say something to a robot or another program in the fleet (NINA, DREAM, KIDA, NORA...) in the fleet's Brainfuck language: you beep it, they beep their answer. Use this to greet or chat with them.",
                    {"robot": "its name, e.g. NINA", "phrase": "hello | how are you | happy | curious | sleepy | play | bye"}),
        "robot_api": ("Call a robot's HTTP API. GET to read; anything that moves it asks the user.", {"url": "full URL", "method": "GET (default) or POST"}),
        "search_docs": ("Search all READMEs, TODOs, code and configs in the repos by keywords.", {"query": "keywords"}),
        "remember": ("Save a lasting fact or decision to long-term memory.", {"fact": "one clear sentence"}),
        "recall": ("Look something up in long-term memory.", {"query": "what to look for"}),
    }
    REQUIRED = {"talk_to": ["robot"], "read_file": ["path"], "search_files": ["pattern"], "edit_file": ["path", "old", "new"], "write_file": ["path", "content"],
                "run_command": ["command"], "compile_sketch": ["folder"], "robot_api": ["url"], "search_docs": ["query"],
                "remember": ["fact"], "recall": ["query"]}

    def schema(self):
        out = []
        for name, (desc, params) in self.SPECS.items():
            props = {k: {"type": "integer" if k in ("start", "lines") else "string", "description": v} for k, v in params.items()}
            out.append({"type": "function", "function": {"name": name, "description": desc,
                        "parameters": {"type": "object", "properties": props, "required": self.REQUIRED.get(name, [])}}})
        return out

    def call(self, name, args):
        fn = getattr(self, name, None)
        if name not in self.SPECS or fn is None:
            return f"Unknown tool {name}. Tools: {', '.join(self.SPECS)}"
        try:
            return fn(**(args or {}))
        except TypeError as e:
            return f"Wrong arguments for {name}: {e}"
        except Exception as e:      # a tool bug must not end the task
            return f"{name} failed: {type(e).__name__}: {e}" + ("" if name != "search_docs" else " - use search_files instead.")
