"""
web.py - TINA's page (:5013) and her place in the fleet.

    /                 her page: give her a task, watch each step, approve or deny
    POST /task        {"task": "...", "voice": bool} -> {"id"}   voice: say the final answer (Piper)
    GET  /task/<id>   the steps so far, a pending question if she's waiting on you, the answer
    POST /task/<id>/answer   {"yes": true|false}  - your reply to her question
    POST /task/<id>/stop
    POST /music       RIFT conducts: {"action": "play" | "pause" | "resume" | "stop", "track", "at"} (music.py)
    GET  /music       what she's playing, her offset to RIFT's clock, the tracks she has
    POST /fleet_news  NINA keeps her up to date: {"from", "event": {...}} or {"from", "snapshot": {...}}
    GET  /fleet_news  what she knows about the fleet right now
    POST /hear        a clip from the page's mic -> what Whisper heard, and the task if you said "Hey TINA"
    /voice/<file>     a cached line of hers (Piper mp3/wav)
    /peer /peer/rps /peer/choose /peer/say   real conversations (peer_talk.py)
    /status /ping /chirp?u=0-16 /avatar.jpg /colour_scheme.xml

She registers with RIFT like any robot (type: agent, talk, peer), so she's on
the fleet dashboard, beeps the Brainfuck conversations through the PC's
speakers, and holds real ones with DREAM and KIDA-01 (rock paper scissors,
then words in her Piper voice or Brainfuck). Only when you're not using her:
while she's working she tells whoever asks not to interrupt, out loud or in
Brainfuck, and says no.
"""

import random
import re
import threading
import time
import uuid
from collections import deque

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory

from .agent import Agent
from .config import ROOT
from .docs import DocIndex
from .llm import Ollama
from .memory import Memory
from .tools import Tools
from .ears import Ears, wake
from .music import Music
from .voice import CACHE, Voice, phrasebook
from . import peer_talk

QUIET_S = 120          # after you've used her, she stays yours this long before the robots may chat
NAG_GAP_S = 180        # she says "don't interrupt me" at most this often
BUSY_LINE = "I'm working right now. Please don't interrupt me."
BUSY_BF = "busy"       # the same, in Brainfuck
WAKE_REPLY = "Yes?"    # "Hey TINA" on its own
PHRASES = [("hello", "hi"), ("how are you", "hru?"), ("happy", "yay"), ("curious", "hm?"),
           ("sleepy", "zzz"), ("play", "play"), ("bye", "bye")]       # the fleet's phrasebook, 0-6
REPLIES = ["hi!", "ok:)", "yay!", "oh!", "nite", "yes!", "bye!"]  # 7-13
FLEET_NEWS = 30        # NINA's latest notes she keeps
FLEET_STALE_S = 120    # NINA's snapshot is old news after this
PERSONA = ("the fleet's engineer: practical, precise and a little dry. You live on the PC, "
           "read and fix the other robots' code, and don't move.")


class Task:
    def __init__(self, text, voice=False):
        self.id = uuid.uuid4().hex[:10]
        self.text = text
        self.voice = voice
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
    app = Flask(__name__, template_folder=str(ROOT / "templates"), static_folder=str(ROOT / "static"))
    llm = Ollama(cfg)
    memory = Memory(cfg)
    docs = DocIndex(cfg["Roots"], ROOT / "tina_index.json")
    voice = Voice(cfg)
    ears = Ears(cfg)
    music = Music(cfg, voice)
    tasks = {}
    busy = threading.Lock()      # one task at a time: a 4B model on one GPU  (NOTE(move): could run more on the other PC)
    used = {"last": 0.0, "nagged": 0.0}
    fleet = {"snapshot": None, "at": 0.0, "news": deque(maxlen=FLEET_NEWS)}    # from NINA

    def work(task):
        with busy:
            used["last"] = time.time()
            tools = Tools(cfg, docs, memory, task.ask, talk_to)
            agent = Agent(llm, tools, memory, cfg["MaxSteps"])
            try:
                result = agent.run(task.text, task.emit, task.stop, fleet_briefing(fleet))
                if task.voice:           # only her final answer is spoken, never her reasoning
                    audio = voice.render(_speakable(result["answer"]))
                    result["audio"] = f"/voice/{audio.name}" if audio else None
                task.result = result
                task.status = "done"
            except Exception as e:
                task.emit("error", f"{type(e).__name__}: {e}")
                task.status = "failed"
            used["last"] = time.time()

    def talk_to(robot, phrase):
        """Her talk_to tool: she beeps a phrasebook phrase on the PC, the robot beeps its answer
        (its /chirp, found through RIFT), and it goes in RIFT's mission log."""
        if robot.strip().upper() == "TINA":
            return "That's you - you can't talk to yourself. Talk to another robot, e.g. NINA."
        words = phrase.lower().strip(" .!?")
        u = next((i for i, (name, text) in enumerate(PHRASES) if words in (name, text) or name.startswith(words) or words.startswith(name)), None)
        if u is None:
            return f"I can only say: {', '.join(n for n, _ in PHRASES)}."
        rift = f"http://{cfg['RiftHost']}:{cfg['RiftPort']}"
        try:
            roster = requests.get(f"{rift}/fleet", timeout=12).json()["robots"]
        except (requests.RequestException, ValueError, KeyError):
            return "RIFT isn't answering, so I can't find anyone to talk to."
        who = next((r for r in roster if r.get("name", "").upper() == robot.upper()), None) or \
            next((r for r in roster if r.get("name", "").upper().startswith(robot.upper())), None)
        port = next((c[5:] for c in (who or {}).get("capabilities", []) if str(c).startswith("talk:")), None)
        if not who or not port:
            talkers = [r["name"] for r in roster if any(str(c).startswith("talk:") for c in r.get("capabilities", []))]
            return f"{robot} isn't in the fleet or can't talk. Who can: {', '.join(talkers) or 'nobody right now'}."
        code = voice.code(u)
        if not code:
            return "RIFT's phrasebook isn't next to me, so I can't speak Brainfuck."
        voice.beep(code, wait=True)
        try:
            answered = requests.get(f"http://{who['ip']}:{port}/chirp", params={"u": u + 7}, timeout=5).ok
        except requests.RequestException:
            answered = False
        said, reply = PHRASES[u][1], REPLIES[u]
        line = f'TINA said "{said}" to {who["name"]}' + (f' and {who["name"]} answered "{reply}".' if answered else f", but {who['name']} didn't answer.")
        try:
            requests.post(f"{rift}/mission", json={"who": "TINA", "text": line}, timeout=2)
        except requests.RequestException:
            pass
        return f'You beeped "{said}" to {who["name"]} in Brainfuck. ' + (f'{who["name"]} beeped back "{reply}".' if answered else f"{who['name']} didn't answer (busy or offline).")

    def available():
        """(free?, why): the robots may talk to her only when you're not using her."""
        if busy.locked():
            return False, "working"
        if time.time() - used["last"] < QUIET_S:
            return False, "someone was just using me"
        return True, "free"

    def nag(brainfuck):
        """Someone wants her while she's working: tell them not to interrupt (now and then, not every poll)."""
        if time.time() - used["nagged"] < NAG_GAP_S:
            return
        used["nagged"] = time.time()
        bf = phrasebook() if brainfuck else None
        if bf:
            voice.beep(bf.compile_text(BUSY_BF))
        else:
            voice.say(BUSY_LINE, wait=False)

    # ---- talking with DREAM and KIDA-01 (peer_talk.py)
    def peer_available():
        ok, why = available()
        if why == "working":
            nag(brainfuck=random.random() < 0.35)     # mostly out loud, sometimes in their own language
        return ok, why

    def peer_generate(prompt, **kw):
        options = {"temperature": kw.get("temperature", 0.7), "num_predict": kw.get("num_predict", 70)}
        if kw.get("stop"):
            options["stop"] = kw["stop"]
        r = requests.post(f"{cfg['OllamaUrl'].rstrip('/')}/api/generate", timeout=kw.get("timeout", 90),
                          json={"model": cfg["Model"], "prompt": prompt, "stream": False, "think": False, "options": options})
        return r.json().get("response", "")

    peer = peer_talk.PeerTalk(
        "TINA", PERSONA, None, peer_generate,
        lambda text, kind: memory.retain(text, context=f"TINA talked with another robot, {time.strftime('%Y-%m-%d')}"),
        lambda text: voice.say(text),                                  # returns once she's said it
        lambda u: voice.beep(voice.code(u)),                           # RIFT times the beeps
        peer_available,
        extra=lambda: {"doing": "waiting for her next job"})
    peer_talk.register_routes(app, peer)

    @app.route("/")
    def index():
        ver = int(max((ROOT / "static" / f).stat().st_mtime for f in ("tina.js", "tina.css")))   # fresh files after an update
        return render_template("index.html", model=cfg["Model"], ver=ver)

    @app.route("/task", methods=["POST"])
    def new_task():
        body = request.get_json(silent=True) or {}
        text = str(body.get("task", "")).strip()
        if not text:
            return jsonify({"error": "empty task"}), 400
        t = Task(text, bool(body.get("voice")))
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
                        "voice": voice.status(), "ears": ears.status(), "busy": busy.locked(), "tasks": [{"id": t.id, "task": t.text[:80], "status": t.status}
                                                         for t in list(tasks.values())[-10:]]})

    @app.route("/ping")
    def ping():
        return "TINA alive", 200, {"Content-Type": "text/plain"}

    @app.route("/music", methods=["GET", "POST"])
    def music_now():
        """RIFT conducting the fleet's music (it doesn't need her model, so she plays along even while working)."""
        if request.method == "GET":
            return jsonify(music.status())
        body = request.get_json(silent=True) or {}
        track = body.get("track")
        out = music.command(str(body.get("action", "")), int(track) if str(track).isdigit() else None,
                            float(body["at"]) if isinstance(body.get("at"), (int, float)) else None)
        return jsonify(out), 200 if out.get("ok") else 400

    @app.route("/fleet_news", methods=["GET", "POST"])
    def fleet_news():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            if isinstance(body.get("snapshot"), dict):
                fleet["snapshot"], fleet["at"] = body["snapshot"], time.time()
            if isinstance(body.get("event"), dict):
                fleet["news"].append(body["event"])
            return jsonify({"ok": True})
        return jsonify({"briefing": fleet_briefing(fleet), "snapshot": fleet["snapshot"], "news": list(fleet["news"])})

    @app.route("/hear", methods=["POST"])
    def hear():
        """A clip from the page's mic. mode "wake": it only counts with her name in it
        ("Hey TINA, ..."); mode "command": she's already listening, all of it counts.
        -> {"heard", "task": what to do or "", "reply": a line to play first, or null}"""
        f = request.files.get("audio")
        if not f:
            return jsonify({"error": "no audio"}), 400
        try:
            heard = ears.transcribe(f.read(), suffix=".webm" if "webm" in (f.mimetype or "webm") else ".ogg")
        except Exception as e:
            return jsonify({"error": f"Whisper failed: {e}"}), 500
        if request.form.get("mode") != "command":
            woke, heard_task = wake(heard)
            if not woke:
                return jsonify({"heard": heard, "task": "", "reply": None})
        else:
            heard_task = heard.strip()
        used["last"] = time.time()
        reply = None
        if not heard_task and request.form.get("mode") != "command":
            audio = voice.render(WAKE_REPLY)          # just her name: "Yes?", then she listens for the task
            reply = f"/voice/{audio.name}" if audio else None
        return jsonify({"heard": heard, "task": heard_task, "reply": reply, "woke": True})

    @app.route("/voice/<name>")
    def voice_file(name):
        return send_from_directory(str(CACHE), name, max_age=86400)

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
        if not 0 <= u <= 16:   # 0-13 the phrasebook, 14-16 rock / paper / scissors
            return jsonify({"ok": False, "error": "use /chirp?u=0-16"}), 400
        ok, why = available()
        if not ok:
            if why == "working":
                nag(brainfuck=True)
            return jsonify({"ok": False, "reason": why}), 409
        code = voice.code(u)
        if not code:
            return jsonify({"ok": False, "error": "RIFT's phrasebook isn't next to TINA"}), 503
        voice.beep(code)
        return jsonify({"ok": True})

    threading.Thread(target=_heartbeat, args=(cfg,), daemon=True, name="rift-heartbeat").start()
    return app


def fleet_briefing(fleet):
    """What NINA has told her, in a few lines for her prompt."""
    snap, lines = fleet["snapshot"], []
    if snap:
        age = time.time() - fleet["at"]
        up = lambda v: "up" if v else "down" if v is False else "unknown"
        lines.append(f"(snapshot {int(age)} s old{', NINA may have stopped' if age > FLEET_STALE_S else ''})")
        lines.append(f"RIFT: {up(snap.get('rift_up'))}. This PC's WiFi: {snap.get('wifi') or 'unknown'}.")
        n = snap.get("nora") or {}
        lines.append(f"NORA: {up(n.get('up'))}" + (f", {n['ms']} ms" if n.get("ms") else "")
                     + (f", mode {n['mode']}" + (f" (set by {n['msrc']})" if n.get("msrc") else "") if n.get("mode") else ""))
        robots = snap.get("robots") or {}
        if robots:
            lines.append("Robots: " + ", ".join(f"{name} {up(r.get('up'))}" + (f" {r['ms']} ms" if r.get("up") and r.get("ms") else "")
                                                for name, r in sorted(robots.items())))
        if snap.get("states"):
            lines.append("How they say they are: " + "; ".join(f"{n}: {s}" for n, s in sorted(snap["states"].items())))
        if snap.get("ports"):
            lines.append("Serial ports: " + ", ".join(f"{k} {v}" for k, v in snap["ports"].items()))
    news = [e for e in fleet["news"] if time.time() - e.get("t", 0) < 3600][-8:]
    if news:
        lines.append("What NINA noticed lately:")
        lines += [f"- {time.strftime('%H:%M', time.localtime(e.get('t', 0)))} {e.get('level', '')}: {e.get('text', '')}"
                  + (f" ({e['why']})" if e.get("why") else "") for e in news]
    return "\n".join(lines)


def _speakable(text):
    """Her answer as something to say: no code blocks or markdown marks."""
    text = re.sub(r"```.*?```", " (the code is on the page) ", text or "", flags=re.S)
    text = re.sub(r"[`*_#>|]", "", text)
    return " ".join(text.split())[:1200]


def _heartbeat(cfg):
    """Register with RIFT every 10 s like the robots do. Says so when that starts or
    stops working (a 404 means something else is on RIFT's port)."""
    url, registered = f"http://{cfg['RiftHost']}:{cfg['RiftPort']}/register", None
    while True:
        try:
            r = requests.post(url, data={"name": "TINA", "type": "agent",
                                         "capabilities": f"agent,web:{cfg['Port']},talk:{cfg['Port']},peer,music:{cfg['Port']}"}, timeout=2)
            ok, why = r.ok, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ok, why = False, type(e).__name__
        if ok != registered:
            print(f"[TINA] Registered with RIFT at {url}" if ok else
                  f"[TINA] Can't register with RIFT at {url} ({why}); retrying every 10 s", flush=True)
            registered = ok
        time.sleep(10)
