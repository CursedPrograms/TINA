"""web.py + agent.py follow-ups: a task sees the last few of its conversation (question, short
answer, files), per caller; NEW CONVERSATION, "fresh" and a long pause start over.
A scripted model and a stubbed memory: no Ollama, nothing written to her long-term memory."""
import time

import pytest

import tina.web as web
from tina.config import load


class NoMemory:
    queries = []

    def __init__(self, cfg):
        pass

    def recall(self, q):
        NoMemory.queries.append(q)
        return []

    def retain(self, *a, **k):
        pass

    def status(self):
        return {"reachable": False, "pending": 0}


@pytest.fixture
def app(monkeypatch):
    seen = []

    class Model:
        def __init__(self, cfg):
            self.n = 0

        def available(self):
            return True

        def chat(self, messages, tools=None, timeout=600):
            self.n += 1
            if messages[-1]["role"] == "user":
                seen.append([dict(m) for m in messages])
                if "port" in messages[-1]["content"].lower():
                    return {"role": "assistant", "content": "", "thinking": "Read NINA's config.",
                            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "NINA/config.json", "lines": 3}}}]}
                return {"role": "assistant", "content": f"Answer {self.n}."}
            return {"role": "assistant", "content": "NINA uses port 5012.\n```json\n\"Port\": 5012\n```"}

    monkeypatch.setattr(web, "Memory", NoMemory)
    monkeypatch.setattr(web, "Ollama", Model)
    monkeypatch.setattr(web, "_heartbeat", lambda cfg: None, raising=False)
    client = web.create_app(load()).test_client()
    client.seen = seen
    return client


def run(c, task, **extra):
    tid = c.post("/task", json=dict(task=task, **extra)).get_json()["id"]
    for _ in range(400):
        d = c.get(f"/task/{tid}").get_json()
        if d["status"] != "running":
            return d
        time.sleep(0.05)
    raise TimeoutError(task)


def test_first_task_has_no_history(app):
    d = run(app, "Which port does NINA use? Check her config.")
    assert d["context"] == 0 and len(app.seen[-1]) == 2
    assert d["result"]["files"] == ["NINA/config.json"]


def test_follow_up_sees_the_last_task(app):
    run(app, "Which port does NINA use? Check her config.")
    d = run(app, "and TINA's?")
    m = app.seen[-1]
    assert d["context"] == 1 and [x["role"] for x in m] == ["system", "user", "assistant", "user"]
    assert m[1]["content"].startswith("Which port does NINA use")
    assert "NINA uses port 5012" in m[2]["content"] and "(code)" in m[2]["content"] and "```" not in m[2]["content"]
    assert "(Files: NINA/config.json)" in m[2]["content"]
    assert "part of a conversation" in m[0]["content"]
    assert all(x["role"] != "tool" for x in m)                     # no old tool output
    assert any(e["kind"] == "context" for e in d["events"])
    assert NoMemory.queries[-1].endswith("and TINA's?") and NoMemory.queries[-1].startswith("Which port")


def test_keeps_three(app):
    for i in range(5):
        run(app, f"task {i}")
    assert len(app.get("/conversation").get_json()["turns"]) == web.CONVO_TURNS
    assert len(app.seen[-1]) == 2 + 2 * web.CONVO_TURNS


def test_dream_has_its_own(app):
    run(app, "on the page")
    assert run(app, "from DREAM", **{"from": "DREAM"})["context"] == 0
    d = run(app, "again from DREAM", **{"from": "DREAM"})
    assert d["context"] == 1 and app.seen[-1][1]["content"] == "from DREAM"


def test_new_conversation_fresh_and_pause(app, monkeypatch):
    run(app, "one")
    app.post("/conversation/new", json={"from": "page"})
    assert run(app, "two")["context"] == 0
    assert run(app, "three", fresh=True)["context"] == 0
    monkeypatch.setattr(web, "CONVO_IDLE_S", 0)
    assert run(app, "after a long pause")["context"] == 0
