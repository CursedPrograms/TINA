"""
TINA - Task Intelligence & Network Assistant: the DREAM Robotics fleet's working agent.

    python tina.py            her page on http://127.0.0.1:5013/ (run.bat starts this)
    python tina.py --cli      talk to her in this terminal; she asks y/n before risky steps
    python tina.py --cli "fix the compile error in MILA's ESP8266 sketch"

She plans, works one step at a time with her tools (files, search, commands,
compile checks, the fleet's APIs, the docs, long-term memory) and checks what
she did before she says it's done.
"""

import sys

from tina.config import load


def cli(cfg, first=None):
    from tina.agent import Agent
    from tina.config import ROOT
    from tina.docs import DocIndex
    from tina.llm import Ollama
    from tina.memory import Memory
    from tina.tools import Tools

    llm, memory = Ollama(cfg), Memory(cfg)
    docs = DocIndex(cfg["Roots"], ROOT / "tina_index.json")
    if not llm.available():
        print(f"TINA: {cfg['Model']} isn't in Ollama yet. Run:  ollama pull {cfg['Model']}")
        return 1

    def ask(question):
        return input(f"\n?? {question} [y/N] ").strip().lower() in ("y", "yes")

    def emit(kind, data):
        if kind == "think":
            print(f"  (thinking) {data[:300]}{'...' if len(data) > 300 else ''}")
        elif kind == "say":
            print(f"TINA: {data}")
        elif kind == "tool":
            args = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in data["args"].items())
            print(f"  -> {data['name']}({args})")
        elif kind == "result":
            text = str(data["text"])
            print("     " + text[:400].replace("\n", "\n     ") + (" ..." if len(text) > 400 else ""))
        elif kind == "memory":
            print("  (remembers) " + " | ".join(m[:80] for m in data))
        elif kind == "error":
            print(f"  !! {data}")

    print(f"TINA ({cfg['Model']}) - type a task, or 'quit'.")
    while True:
        try:
            task = first or input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        first = None
        if task.lower() in ("quit", "exit", "q"):
            return 0
        if not task:
            continue
        result = Agent(llm, Tools(cfg, docs, memory, ask), memory, cfg["MaxSteps"]).run(task, emit)
        print(f"\nTINA: {result['answer']}\n  ({result['steps']} steps, {result.get('seconds', '?')} s)")


def main():
    cfg = load()
    args = sys.argv[1:]
    if args and args[0] == "--cli":
        return cli(cfg, " ".join(args[1:]) or None)
    from tina.web import create_app
    app = create_app(cfg)
    print(f"TINA on http://127.0.0.1:{cfg['Port']}/  (model {cfg['Model']})")
    app.run(host="0.0.0.0", port=cfg["Port"], debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
