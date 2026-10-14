"""
drive.py - TINA drives a robot by its camera: stop, look, think, move (fleet TODO §1a).

A slow model can drive if the robot stands still while it thinks. Each step:

  look   one fresh frame from the robot's camera (KIDA01: /video_feed/0, else /last_photo)
  think  a vision model (VisionModel, gemma3:4b-it-qat) says what's in front, where there's
         room, and picks ONE move, with how sure it is
  move   that move through RIFT (POST /talk verb do), at most MAX_MOVE_S, then she stops
         and looks again

until the goal is reached, the steps run out, or anything is in doubt: no picture, an
answer that doesn't parse, a move the robot can't make, the model not sure enough, or
the robot not doing what it was told. Then the robot is told to stop.

A person says yes once, before the first move, for the whole drive (its goal, how many
moves, how long each), the way tell_robot asks before anything that moves a robot. RIFT
gets "confirmed": true for those moves only; it never relays unlocking, and the robot
keeps its own emergency stop.
"""

import base64
import io
import json
import time

import requests

MOVES = ["forward", "back", "turn_left", "turn_right"]
MAX_MOVE_S = 1.0       # each move is short: she looks again before the next
MAX_BACK_S = 0.5       # backing up is blind (the camera looks forward): shorter still
SETTLE_S = 0.4         # after a move, let her stop rocking before the next picture
SURE_ENOUGH = 0.6      # below this, she stops instead of guessing
FRAME_W = 640          # the picture is shrunk to this width for the model

PROMPT = """You are driving {robot}, a small tank robot, by its front camera. The picture is what the camera sees right now; the robot is standing still.
Goal: {goal}
Moves so far: {history}

Look at the floor in front of the robot. Say what you see, how much room there is to the left, ahead and to the right, then choose ONE move:
- forward / back: drive straight for "seconds" (0.3 to 1.0)
- turn_left / turn_right: turn on the spot for "seconds" (0.3 to 1.0; 0.5 is about a quarter turn)
- done: the goal is reached
- stop: nothing is safe, or you can't tell
"sure" is how sure you are that the move is safe and helps the goal, from 0 to 1. Never drive forward into something close."""

SCHEMA = {
    "type": "object",
    "properties": {
        "see": {"type": "string"},
        "room": {"type": "object", "properties": {k: {"type": "string", "enum": ["open", "some", "blocked"]}
                                                  for k in ("left", "ahead", "right")},
                 "required": ["left", "ahead", "right"]},
        "action": {"type": "string", "enum": MOVES + ["done", "stop"]},
        "seconds": {"type": "number"},
        "sure": {"type": "number"},
        "why": {"type": "string"},
    },
    "required": ["see", "room", "action", "seconds", "sure", "why"],
}


def _shrink(jpeg):
    """The frame as a smaller JPEG, base64 (the model is faster with fewer pixels)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
        if im.width > FRAME_W:
            im = im.resize((FRAME_W, round(im.height * FRAME_W / im.width)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        jpeg = buf.getvalue()
    except Exception:
        pass                                   # no Pillow, or not an image Pillow knows: send it as it is
    return base64.b64encode(jpeg).decode()


def frame_from(base, timeout=6):
    """One fresh JPEG from the robot: the first frame of its MJPEG stream, else its last photo."""
    try:
        with requests.get(f"{base}/video_feed/0", stream=True, timeout=timeout) as r:
            if r.ok:
                buf, started = b"", time.time()
                for chunk in r.iter_content(8192):
                    buf += chunk
                    a = buf.find(b"\xff\xd8")
                    b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
                    if b > a >= 0:
                        return buf[a:b + 2]
                    if time.time() - started > timeout or len(buf) > 4_000_000:
                        break
    except requests.RequestException:
        pass
    try:
        r = requests.get(f"{base}/last_photo", timeout=timeout)
        if r.status_code == 200 and r.content[:2] == b"\xff\xd8":
            return r.content
    except requests.RequestException:
        pass
    return None


class Driver:
    def __init__(self, cfg, relay, ask):
        self.cfg, self.relay, self.ask = cfg, relay, ask
        self.rift = f"http://{cfg['RiftHost']}:{cfg['RiftPort']}"
        self.ollama = cfg["OllamaUrl"].rstrip("/")
        self.model = cfg.get("VisionModel", "gemma3:4b-it-qat")

    def base_of(self, robot):
        fleet = requests.get(f"{self.rift}/fleet", timeout=8).json().get("robots", [])
        r = next((x for x in fleet if str(x.get("name", "")).upper() == robot.upper()), None)
        if not r:
            return None
        return (r.get("url") or f"http://{r.get('ip')}/").rstrip("/")

    def think(self, jpeg, robot, goal, history):
        body = {"model": self.model, "stream": False, "format": SCHEMA, "options": {"temperature": 0.2},
                "messages": [{"role": "user", "images": [_shrink(jpeg)],
                              "content": PROMPT.format(robot=robot, goal=goal, history="; ".join(history) or "none yet")}]}
        r = requests.post(f"{self.ollama}/api/chat", json=body, timeout=180)
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])

    def drive(self, robot, goal, steps=6):
        """Returns a plain report: each step (what she saw, what she did, how long it took) and why she stopped."""
        steps = max(1, min(12, int(steps or 6)))
        lines, history, moved, allowed, pace = [], [], False, False, []
        try:
            base = self.base_of(robot)
        except (requests.RequestException, ValueError):
            return "RIFT isn't answering, so I can't find the robot."
        if not base:
            return f"{robot} isn't online (not on RIFT's roster)."
        outcome = f"Used all {steps} steps without reaching the goal."
        for n in range(1, steps + 1):
            t0 = time.time()
            jpeg = frame_from(base)
            t1 = time.time()
            if not jpeg:
                outcome = "Stopped: no picture from her camera."
                break
            try:
                plan = self.think(jpeg, robot, goal, history)
            except (requests.RequestException, ValueError, KeyError) as e:
                outcome = f"Stopped: the vision model didn't answer properly ({type(e).__name__})."
                break
            t2 = time.time()
            action, sure = str(plan.get("action", "")).lower(), float(plan.get("sure") or 0)
            room = plan.get("room") or {}
            seen = (f"{n}. saw: {str(plan.get('see', ''))[:200]} | room left {room.get('left')}, ahead {room.get('ahead')}, "
                    f"right {room.get('right')} | why: {str(plan.get('why', ''))[:160]}")
            if action == "done":
                lines.append(seen + f" -> done (look {t1 - t0:.1f} s, think {t2 - t1:.1f} s)")
                outcome = "Goal reached, she says."
                break
            if action not in MOVES:
                lines.append(seen + " -> stop")
                outcome = "Stopped: the model saw nothing safe to do."
                break
            if sure < SURE_ENOUGH:
                lines.append(seen + f" -> wanted {action}, but only {sure:.2f} sure")
                outcome = f"Stopped: not sure enough ({sure:.2f} < {SURE_ENOUGH})."
                break
            if action == "forward" and room.get("ahead") == "blocked":
                lines.append(seen + " -> wanted forward into what it called blocked")
                outcome = "Stopped: the model contradicted itself (forward, with the way ahead blocked)."
                break
            cap = MAX_BACK_S if action == "back" else MAX_MOVE_S
            seconds = round(max(0.3, min(cap, float(plan.get("seconds") or 0.5))), 2)
            if not allowed:
                if not self.ask(f"TINA wants to drive {robot} by her camera toward: {goal}. Up to {steps} moves of at most "
                                f"{MAX_MOVE_S:g} s each, looking before every move and stopping when unsure. Allow?"):
                    outcome = "The user said no; she didn't move."
                    lines.append(seen + f" -> would have: {action} {seconds} s")
                    break
                allowed = True
            out = self.relay({"to": robot, "verb": "do", "action": action, "seconds": seconds, "confirmed": True})
            moved = True
            if not out.get("ok"):
                lines.append(seen + f" -> {action} {seconds} s: refused")
                outcome = f"Stopped: {robot} didn't do it ({out.get('error') or out})."
                break
            time.sleep(seconds + SETTLE_S)       # the robot answers at once and drives on its own: still before the next look
            t3 = time.time()
            lines.append(seen + f" -> {action} {seconds} s, sure {sure:.2f} "
                                f"(look {t1 - t0:.1f} s, think {t2 - t1:.1f} s, move {t3 - t2:.1f} s)")
            pace.append(t3 - t0)
            history.append(f"{action} {seconds}s")
        if moved:
            self.relay({"to": robot, "verb": "do", "action": "stop"})
        if pace:
            lines.append(f"Pace: {sum(pace) / len(pace):.1f} s a move on average ({len(pace)} moves).")
        return outcome + "\n" + "\n".join(lines)
