"""
agent.py - TINA's loop: understand, plan, act one step, check, repeat, report.

The model sees the task, what she remembers about it, and her tools. Each
turn it either calls one tool or answers. Tool results go back in, trimmed so a
small context window doesn't fill up with old output. When she's done she
reports what she did and what she checked, and the gist goes to long-term
memory so the next task starts knowing it.
"""

import json
import re
import time
from pathlib import Path

from .llm import LLMError

SYSTEM = """You are TINA, the Task Intelligence & Network Assistant of the DREAM Robotics fleet: robots (NORA, MILA, WHIP, IDA, KIDA, ARM) and the programs around them (RIFT the fleet hub, DREAM the companion, NINA the caretaker, LYCEA the training school). You do real work in their repos for the user.

How you work:
- Start with a 1-3 line plan. Then do ONE step at a time with a tool and look at the result before the next step.
- Look before you change: read or search the code first. Never guess file contents.
- A robot's code is in its repo folder (MILA -> MILA-Robot-v00). Robot firmware is Arduino: .ino, .cpp and .h files, not just .py.
- To find something (a part like "hailo", a feature, an error), search_files its repo for the word first, then read_file around the hits with start=. Don't read big files from the top.
- Only say what you read in the code. If you couldn't find the code, say that instead of explaining from guesses.
- Edit with edit_file, copying the old text exactly. Keep changes small and in the style of the code around them.
- Check your work: compile, run py_compile, or read the file back. Never say something works unless you checked it.
- If something fails twice, stop and explain what you found instead of looping.
- Stay on the user's task. If you notice another problem on the way, mention it in your report; don't go fixing it.
- Fleet news has times: an older problem that a newer note says is fixed is over.
- Use search_docs to find where things are; use recall for what you learned before; remember lasting decisions.
- Finish with a short plain report: what you did, which files, what you checked and the result, and anything left to do.
Be brief. No markdown tables."""

KEEP_FULL = 4          # the newest tool results stay whole; older ones are trimmed
TRIM_TO = 400


def _repos(roots):
    """The repos she works in, so a robot's name leads her to its folder."""
    lines = []
    for root in roots:
        try:
            names = sorted(p.name for p in Path(root).iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
        except OSError:
            continue
        lines.append(f"{root}: " + ", ".join(names))
    return "\n\nThe repos (paths are relative to the first folder):\n" + "\n".join(lines) if lines else ""


WRITTEN_CALL = re.compile(r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^{}]*\})\s*\}', re.S)


def _written_call(text, tools):
    """qwen3 sometimes writes a tool call into its reply ({"name": ..., "arguments": {...}},
    maybe in <tool_call> tags) instead of making it. That's a call, not her answer."""
    m = WRITTEN_CALL.search(text or "")
    if not m or m.group(1) not in tools:
        return None
    try:
        args = json.loads(m.group(2))
    except ValueError:
        return None
    return {"function": {"name": m.group(1), "arguments": args}}


def _guide():
    """fleet_guide.md: who's who in the fleet and where their code is, so she doesn't have to work it out."""
    try:
        return "\n\nThe fleet:\n" + (Path(__file__).resolve().parents[1] / "fleet_guide.md").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Agent:
    def __init__(self, llm, tools, memory, max_steps=25):
        self.llm, self.tools, self.memory, self.max_steps = llm, tools, memory, max_steps

    def _trim(self, messages):
        tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
        for i in tool_idx[:-KEEP_FULL]:
            c = messages[i]["content"]
            if len(c) > TRIM_TO:
                messages[i]["content"] = c[:TRIM_TO] + " ... [older output trimmed]"

    def run(self, task, emit=lambda kind, data: None, stop=None, fleet=""):
        """Do the task. emit(kind, data) reports progress: plan/think, tool, result, answer, error.
        fleet: what NINA last told her about the fleet (web.py's fleet_briefing)."""
        started = time.time()
        recalled = self.memory.recall(task)
        system = SYSTEM + _guide() + _repos(self.tools.roots)
        if fleet:
            system += "\n\nThe fleet right now, as NINA (the caretaker) last told you:\n" + fleet
        if recalled:
            system += "\n\nWhat you remember that may matter:\n" + "\n".join(f"- {m}" for m in recalled[:6])
            emit("memory", recalled[:6])
        messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
        tools = self.tools.schema()
        answer, steps = None, 0
        for steps in range(1, self.max_steps + 1):
            if stop is not None and stop.is_set():
                answer = "Stopped by the user."
                break
            try:
                msg = self.llm.chat(messages, tools)
            except LLMError as e:
                emit("error", str(e))
                return {"answer": f"I couldn't reach my model: {e}", "steps": steps, "changes": self.tools.log}
            if msg.get("thinking"):
                emit("think", msg["thinking"].strip()[:1500])
            calls = msg.get("tool_calls") or []
            content = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
            if not calls:
                call = _written_call(content, self.tools.SPECS)
                if call:                  # she wrote the call out as text instead of making it
                    calls, content = [call], ""
            messages.append({"role": "assistant", "content": content, "tool_calls": calls} if calls else
                            {"role": "assistant", "content": content})
            if not calls:
                answer = content or "(no answer)"
                break
            if content:
                emit("say", content)
            for call in calls[:1]:          # one step at a time: she sees each result before the next move
                fn = call.get("function", {})
                name, args = fn.get("name", ""), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                emit("tool", {"name": name, "args": args})
                result = self.tools.call(name, args)
                emit("result", {"name": name, "text": result})
                messages.append({"role": "tool", "tool_name": name, "content": str(result)})
            self._trim(messages)
        else:
            answer = (f"I used all {self.max_steps} steps without finishing. Changes so far: "
                      + ("; ".join(self.tools.log) or "none") + ".")
        emit("answer", answer)
        if self.tools.log or len(task) > 20:   # what happened goes to long-term memory
            self.memory.retain(f"Task: {task[:300]}\nOutcome: {answer[:600]}\nChanges: {'; '.join(self.tools.log)[:400] or 'none'}",
                               context=f"TINA task, {time.strftime('%Y-%m-%d')}")
        return {"answer": answer, "steps": steps, "changes": list(self.tools.log), "seconds": round(time.time() - started)}
