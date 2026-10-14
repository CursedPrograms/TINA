"""drive.py: stop, look, think, move - every stop-on-doubt rule, with a scripted vision model,
a fake KIDA01 camera and a fake RIFT (conftest). No Ollama, no robot."""
import pytest

from tina import drive as D


def plan(action, seconds=0.5, sure=0.9, ahead="some"):
    return {"see": "a floor", "room": {"left": "open", "ahead": ahead, "right": "open"},
            "action": action, "seconds": seconds, "sure": sure, "why": "test"}


@pytest.fixture
def run(cfg, fake, monkeypatch):
    """run(*plans, answer=True, steps=6) -> (report, asked, frames the model got, waits)."""
    waits = []
    monkeypatch.setattr(D.time, "sleep", waits.append)

    def go(*plans, answer=True, steps=6, robot="KIDA01"):
        it, frames, asked = iter(plans), [], []

        def think(self, jpeg, robot, goal, history):
            frames.append(jpeg)
            p = next(it)
            if isinstance(p, Exception):
                raise p
            return p
        monkeypatch.setattr(D.Driver, "think", think)
        relay = lambda body: __import__("requests").post(f"{fake.base}/talk", json=dict(body, **{"from": "TINA"}), timeout=5).json()
        report = D.Driver(cfg, relay, lambda q: asked.append(q) or answer).drive(robot, "find open floor", steps)
        return report, asked, frames, waits
    return go


def moves(fake):
    return [(b["action"], b.get("seconds")) for b in fake.talk if b["verb"] == "do"]


def test_drives_until_done(run, fake):
    report, asked, frames, waits = run(plan("turn_left"), plan("forward", 2.5), plan("done"))
    assert report.startswith("Goal reached") and len(asked) == 1
    assert moves(fake) == [("turn_left", 0.5), ("forward", 1.0), ("stop", None)]     # capped at 1 s
    assert all(f[:2] == b"\xff\xd8" for f in frames)                                  # real JPEG frames
    assert waits == [0.5 + D.SETTLE_S, 1.0 + D.SETTLE_S]                              # still before each look
    assert all(b.get("confirmed") for b in fake.talk if b["verb"] == "do" and b["action"] != "stop")


def test_unsure_stops_without_asking(run, fake):
    report, asked, _, _ = run(plan("forward", sure=0.4))
    assert report.startswith("Stopped: not sure") and asked == [] and moves(fake) == []


def test_model_says_stop(run, fake):
    report, _, _, _ = run(plan("turn_right"), plan("stop"))
    assert "nothing safe" in report and moves(fake)[-1] == ("stop", None)


def test_bad_answer_stops(run, fake):
    report, _, _, _ = run(ValueError("not json"))
    assert report.startswith("Stopped: the vision model") and moves(fake) == []


def test_forward_into_blocked_stops(run, fake):
    report, _, _, _ = run(plan("forward", ahead="blocked"))
    assert report.startswith("Stopped: the model contradicted") and moves(fake) == []


def test_back_is_shorter(run, fake):
    run(plan("back", 1.0), plan("done"))
    assert moves(fake)[0] == ("back", D.MAX_BACK_S)


def test_user_says_no(run, fake):
    report, asked, _, _ = run(plan("forward"), answer=False)
    assert report.startswith("The user said no") and len(asked) == 1 and moves(fake) == []


def test_robot_refuses(run, fake):
    fake.refuse = "forward"
    report, _, _, _ = run(plan("forward"), plan("forward"))
    assert "didn't do it" in report and moves(fake) == [("forward", 0.5), ("stop", None)]


def test_steps_run_out(run, fake):
    report, asked, _, _ = run(*[plan("turn_left")] * 3, steps=3)
    assert report.startswith("Used all 3 steps") and len(asked) == 1
    assert moves(fake) == [("turn_left", 0.5)] * 3 + [("stop", None)]


def test_last_photo_when_no_stream(run, fake):
    fake.stream = False
    report, _, frames, _ = run(plan("done"))
    assert report.startswith("Goal reached") and frames[0] == fake.jpeg


def test_no_picture(run, fake):
    fake.stream = fake.photo = False
    report, _, _, _ = run(plan("forward"))
    assert report.startswith("Stopped: no picture") and moves(fake) == []


def test_robot_offline(run, fake):
    report, _, _, _ = run(plan("forward"), robot="MILA")
    assert report.endswith("isn't online (not on RIFT's roster).")
