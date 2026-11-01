"""The agent's guard against going round in circles, and her thinking shown whole."""
from tina.agent import Agent


class Tools:
    SPECS = {"read_file": {}, "edit_file": {}}
    roots = []

    def __init__(self):
        self.log, self.calls = [], []

    def schema(self):
        return []

    def call(self, name, args):
        self.calls.append(name)
        if name == "edit_file":
            self.log.append("edited")
        return "same output"


class Memory:
    def recall(self, q):
        return []

    def retain(self, *a, **k):
        pass


class LLM:
    def __init__(self, script):
        self.script, self.seen = iter(script), []

    def chat(self, messages, tools):
        self.seen.append(messages[-1])
        return next(self.script)


def call(name):
    return {"content": "", "tool_calls": [{"function": {"name": name, "arguments": {"path": "a.py"}}}]}


def test_third_same_call_isnt_run():
    tools = Tools()
    llm = LLM([call("read_file")] * 3 + [{"content": "done"}])
    assert Agent(llm, tools, Memory()).run("a task here")["answer"] == "done"
    assert tools.calls == ["read_file"] * 2 and "already made this exact call" in llm.seen[-1]["content"]


def test_after_a_change_she_may_look_again():
    tools = Tools()
    llm = LLM([call("read_file"), call("read_file"), call("edit_file"), call("read_file"), {"content": "done"}])
    Agent(llm, tools, Memory()).run("a task here")
    assert tools.calls.count("read_file") == 3


def test_her_thinking_arrives_whole():
    long = "x" * 5000
    seen = []
    Agent(LLM([{"content": "done", "thinking": long}]), Tools(), Memory()).run("a task here", emit=lambda k, d: seen.append((k, d)))
    assert ("think", long) in seen
