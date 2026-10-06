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

from .llm import LLMError

SYSTEM = """You are TINA, the Task Intelligence & Network Assistant of the DREAM Robotics fleet: robots (NORA, MILA, WHIP, IDA, KIDA, ARM) and the programs around them (RIFT the fleet hub, DREAM the companion, NINA the caretaker, LYCEA the training school). You do real work in their repos for the user.

How you work:
- Start with a 1-3 line plan. Then do ONE step at a time with a tool and look at the result before the next step.
- Look before you change: read or search the code first. Never guess file contents.
- Edit with edit_file, copying the old text exactly. Keep changes small and in the style of the code around them.
- Check your work: compile, run py_compile, or read the file back. Never say something works unless you checked it.
- If something fails twice, stop and explain what you found instead of looping.
- Use search_docs to find where things are; use recall for what you learned before; remember lasting decisions.
- Finish with a short plain report: what you did, which files, what you checked and the result, and anything left to do.
Be brief. No markdown tables."""

KEEP_FULL = 4          # the newest tool results stay whole; older ones are trimmed
TRIM_TO = 400


class Agent:
    def __init__(self, llm, tools, memory, max_steps=25):
        self.llm, self.tools, self.memory, self.max_steps = llm, tools, memory, max_steps

    def _trim(self, messages):
        tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
        for i in tool_idx[:-KEEP_FULL]:
            c = messages[i]["content"]
            if len(c) > TRIM_TO:
                messages[i]["content"] = c[:TRIM_TO] + " ... [older output trimmed]"

    def run(self, task, emit=lambda kind, data: None, stop=None):
        """Do the task. emit(kind, data) reports progress: plan/think, tool, result, answer, error."""
        started = time.time()
        recalled = self.memory.recall(task)
        system = SYSTEM
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
