"""tools.py: ask_robot / tell_robot through a fake RIFT. Anything that moves a robot asks the user."""
from tina.tools import Tools


def tools(cfg, answers):
    asked = []

    def ask(q):
        asked.append(q)
        return answers.pop(0) if answers else False
    return Tools(cfg, docs=None, memory=None, ask=ask), asked


def test_ask_robot(cfg, fake):
    t, asked = tools(cfg, [])
    out = t.ask_robot("KIDA01")
    assert '"ok": true' in out and "music_stop" in out
    assert fake.talk == [{"to": "KIDA01", "verb": "ask", "from": "TINA"}] and asked == []


def test_harmless_needs_no_yes(cfg, fake):
    t, asked = tools(cfg, [])
    out = t.tell_robot("NORA", "music_stop")
    assert '"ok": true' in out and asked == [] and t.log == ["told NORA to music_stop"]


def test_moving_asks_and_yes_goes(cfg, fake):
    t, asked = tools(cfg, [True])
    out = t.tell_robot("NORA", "forward", seconds=1)
    assert len(asked) == 1 and "NORA to forward for 1 s" in asked[0] and "moves the robot" in asked[0]
    assert fake.talk[-1] == {"to": "NORA", "verb": "do", "action": "forward", "seconds": 1, "confirmed": True, "from": "TINA"}
    assert '"ok": true' in out


def test_no_is_respected(cfg, fake):
    t, asked = tools(cfg, [False])
    assert t.tell_robot("NORA", "forward", seconds=1) == "The user said no."
    assert len(fake.talk) == 1 and "confirmed" not in fake.talk[0] and t.log == []


def test_arm_motor_and_angle_passed(cfg, fake):
    t, _ = tools(cfg, [True])
    t.tell_robot("ARM", "joint", motor="3", angle="120")
    assert fake.talk[0] == {"to": "ARM", "verb": "do", "action": "joint", "motor": "3", "angle": "120", "from": "TINA"}
    assert fake.talk[1]["confirmed"] is True and fake.talk[1]["motor"] == "3" and fake.talk[1]["angle"] == "120"


def test_empty_extras_left_out(cfg, fake):
    t, _ = tools(cfg, [])
    t.tell_robot("NORA", "music_play", seconds="", motor=None)
    assert fake.talk[0] == {"to": "NORA", "verb": "do", "action": "music_play", "from": "TINA"}


def test_rift_down(cfg):
    t, _ = tools(dict(cfg, RiftPort=9), [])
    assert "RIFT isn't answering" in t.tell_robot("NORA", "music_stop")


def test_drive_by_camera_is_a_tool(cfg):
    t, _ = tools(cfg, [])
    assert "drive_by_camera" in t.SPECS and t.REQUIRED["drive_by_camera"] == ["robot", "goal"]
    assert any(s["function"]["name"] == "drive_by_camera" for s in t.schema())
