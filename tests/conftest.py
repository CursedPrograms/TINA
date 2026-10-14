"""Shared test setup for TINA: offline, no robots, no RIFT.

fake: one local Flask app playing RIFT (/fleet, POST /talk with its safety rule: anything that
moves needs "confirmed") and KIDA01 (an MJPEG /video_feed/0 of a small JPEG, /last_photo).
Run from the TINA folder:  venv\\Scripts\\python -m pytest tests
"""
import os
import sys
import threading

import pytest
from flask import Flask, Response, jsonify, request
from werkzeug.serving import make_server

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

HARMLESS = {"stop", "music_stop", "music_play", "photo", "scan", "lock_wheels"}


def tiny_jpeg():
    """A real (if tiny) JPEG, made without Pillow: 1x1 grey."""
    import base64
    return base64.b64decode(
        "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////"
        "////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/"
        "2gAIAQEAAD8AN//Z")


class Fake:
    def __init__(self):
        self.reset()

    def reset(self):
        self.talk = []            # bodies RIFT got
        self.refuse = None        # an action KIDA01 refuses (409, wheels locked)
        self.stream = True        # /video_feed/0 works
        self.photo = True         # /last_photo works
        self.robots = ["KIDA01", "NORA", "ARM"]


@pytest.fixture(scope="session")
def _server():
    fake = Fake()
    app = Flask("fake_rift_and_kida")
    jpeg = tiny_jpeg()

    @app.route("/fleet")
    def fleet():
        return jsonify({"robots": [{"name": n, "ip": "127.0.0.1", "url": f"{fake.base}/{n.lower()}/"} for n in fake.robots]})

    @app.route("/talk", methods=["POST"])
    def talk():
        d = request.get_json()
        fake.talk.append(d)
        if d["to"] not in fake.robots:
            return jsonify({"ok": False, "error": f"{d['to']} isn't online"}), 404
        if d["verb"] == "ask":
            return jsonify({"ok": True, "to": d["to"], "core": {"moving": False}, "does": sorted(HARMLESS)})
        if d["action"] not in HARMLESS and d.get("confirmed") is not True:
            return jsonify({"ok": False, "needs": "confirmed", "tier": "moving"}), 403
        if d["action"] == fake.refuse:
            return jsonify({"ok": False, "error": "her wheels are locked"}), 409
        return jsonify({"ok": True, "to": d["to"], "action": d["action"]})

    @app.route("/kida01/video_feed/0")
    def feed():
        if not fake.stream:
            return "", 404
        part = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
        return Response(iter([part, part]), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/kida01/last_photo")
    def last_photo():
        return (jpeg, 200, {"Content-Type": "image/jpeg"}) if fake.photo else ("", 204)

    srv = make_server("127.0.0.1", 0, app, threaded=True)
    fake.base = f"http://127.0.0.1:{srv.server_port}"
    fake.port = srv.server_port
    fake.jpeg = jpeg
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield fake
    srv.shutdown()


@pytest.fixture
def fake(_server):
    _server.reset()
    return _server


@pytest.fixture
def cfg(fake, tmp_path):
    return {"RiftHost": "127.0.0.1", "RiftPort": fake.port, "OllamaUrl": "http://127.0.0.1:9",
            "VisionModel": "gemma3:4b-it-qat", "Roots": [str(tmp_path)], "AutoEdit": True}
