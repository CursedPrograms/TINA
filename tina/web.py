"""
web.py - TINA's page (:5013) and her place in the fleet.

    /                 her page: give her a task, watch each step, approve or deny
    POST /task        {"task": "..."} -> {"id"}
    GET  /task/<id>   the steps so far, a pending question if she's waiting on you, the answer
    POST /task/<id>/answer   {"yes": true|false}  - your reply to her question
    POST /task/<id>/stop
    /status /ping /chirp?u=0-13 /avatar.jpg /colour_scheme.xml

She registers with RIFT like any robot (type: agent), so she's on the
fleet dashboard, and joins the Brainfuck conversations on the PC speaker.
"""

import os
import sys
import threading
import time
import uuid

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory

from .agent import Agent
from .config import ROOT
from .docs import DocIndex
from .llm import Ollama
from .memory import Memory
from .tools import Tools


class Task:
    def __init__(self, text):
        self.id = uuid.uuid4().hex[:10]
        self.text = text
        self.events = []
        self.status = "running"
        self.question = None
        self._reply = None
        self._replied = threading.Event()
        self.stop = threading.Event()
        self.result = None

    def emit(self, kind, data):
        self.events.append({"t": time.time(), "kind": kind, "data": data})

    def ask(self, question):
        """Blocks the agent until the user answers on the page."""
        self.question, self._reply = question, None
        self._replied.clear()
        self.emit("ask", question)
        while not self._replied.wait(0.5):
            if self.stop.is_set():
                self.question = None
                return False
        self.question = None
        self.emit("answered", "yes" if self._reply else "no")
        return bool(self._reply)

    def answer(self, yes):
        self._reply = yes
        self._replied.set()


def create_app(cfg):
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    llm = Ollama(cfg)
    memory = Memory(cfg)
    docs = DocIndex(cfg["Roots"], ROOT / "tina_index.json")
    tasks = {}
    busy = threading.Lock()      # one task at a time: a 4B model on one GPU  (NOTE(move): could run more on the other PC)

    def work(task):
        with busy:
            tools = Tools(cfg, docs, memory, task.ask)
            agent = Agent(llm, tools, memory, cfg["MaxSteps"])
            try:
                task.result = agent.run(task.text, task.emit, task.stop)
                task.status = "done"
            except Exception as e:
                task.emit("error", f"{type(e).__name__}: {e}")
                task.status = "failed"

    @app.route("/")
    def index():
        return render_template("index.html", model=cfg["Model"])

    @app.route("/task", methods=["POST"])
    def new_task():
        text = (request.get_json(silent=True) or {}).get("task", "").strip()
        if not text:
            return jsonify({"error": "empty task"}), 400
        t = Task(text)
        tasks[t.id] = t
        threading.Thread(target=work, args=(t,), daemon=True, name=f"tina-{t.id}").start()
        return jsonify({"id": t.id})

    @app.route("/task/<tid>")
    def get_task(tid):
        t = tasks.get(tid)
        if not t:
            return jsonify({"error": "no such task"}), 404
        since = int(request.args.get("since", 0))
        return jsonify({"status": t.status, "events": t.events[since:], "next": len(t.events),
                        "question": t.question, "result": t.result})

    @app.route("/task/<tid>/answer", methods=["POST"])
    def answer(tid):
        t = tasks.get(tid)
        if t:
            t.answer(bool((request.get_json(silent=True) or {}).get("yes")))
        return jsonify({"ok": bool(t)})

    @app.route("/task/<tid>/stop", methods=["POST"])
    def stop(tid):
        t = tasks.get(tid)
        if t:
            t.stop.set()
            t.answer(False)
        return jsonify({"ok": bool(t)})

    @app.route("/status")
    def status():
        return jsonify({"model": cfg["Model"], "model_ready": llm.available(), "memory": memory.status(),
                        "busy": busy.locked(), "tasks": [{"id": t.id, "task": t.text[:80], "status": t.status}
                                                         for t in list(tasks.values())[-10:]]})

    @app.route("/ping")
    def ping():
        return "TINA alive", 200, {"Content-Type": "text/plain"}

    @app.route("/colour_scheme.xml")
    def colours():
        return send_from_directory(str(ROOT), "colour_scheme.xml", mimetype="application/xml")

    @app.route("/avatar.jpg")
    def avatar():
        for name in ("tina_avatar.jpg", "tina_avatar.png"):
            if (ROOT / "images" / name).exists():
                return send_from_directory(str(ROOT / "images"), name, max_age=86400)
        return "", 404

    @app.route("/chirp")
    def chirp():
        try:
            u = int(request.args.get("u", ""))
        except ValueError:
            u = -1
        if not 0 <= u <= 13:
            return jsonify({"ok": False, "error": "use /chirp?u=0-13"}), 400
        threading.Thread(target=_chirp, args=(u, cfg["VoicePitch"]), daemon=True).start()
        return jsonify({"ok": True})

    threading.Thread(target=_heartbeat, args=(cfg,), daemon=True, name="rift-heartbeat").start()
    return app


def _heartbeat(cfg):
    """Register with RIFT every 10 s like the robots do."""
    while True:
        try:
            requests.post(f"http://{cfg['RiftHost']}:{cfg['RiftPort']}/register",
                          data={"name": "TINA", "type": "agent", "capabilities": f"agent,web:{cfg['Port']},talk:{cfg['Port']}"}, timeout=2)
        except requests.RequestException:
            pass
        time.sleep(10)


def _chirp(u, pitch):
    """The fleet's Brainfuck phrases, beeped on the PC speaker (RIFT's phrasebook)."""
    sys.path.insert(0, str(ROOT.parent / "RIFT" / "Fleet"))
    try:
        import brainfuck_talk as bf
    except ImportError:
        return
    code = next((c for i, _, _, _, c in bf.utterances() if i == u), None)
    if code and os.name == "nt":
        import winsound
        for ch in code:
            winsound.Beep(max(37, int(bf.TONES.get(ch, 1000) * pitch)), bf.LENGTHS.get(ch, bf.DEFAULT_MS))
