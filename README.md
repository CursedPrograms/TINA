# TINA

**Task Intelligence & Network Assistant** — the DREAM Robotics fleet's working agent.

TINA is a local LLM agent (Ollama) that does real work across the fleet's repos:
reads and searches code, makes small edits in the style of the surrounding code,
compiles and checks its own work, and reports back plainly. She is the *engineer*
of the fleet, alongside DREAM (the companion), RIFT (the hub) and NINA (the caretaker),
and the robots NORA, MILA, WHIP, IDA, KIDA and ARM.

## What she does

- **Plans, then acts one step at a time** — read/search first, edit small, check the result before the next step.
- **Checks her work** — compiles or re-reads files; never claims something works unless she verified it.
- **Remembers** — long-term memory via the shared **Hindsight** server (memory bank `tina`), so decisions and context carry across sessions.
- **Stays in her lane** — if something fails twice she stops and explains instead of looping.

## Run

```bat
run.bat
```

This sets up the environment and starts her web UI on <http://127.0.0.1:5013/>.

```bat
python tina.py --cli                              talk to her in the terminal (asks before risky steps)
python tina.py --cli "fix the compile error in MILA's ESP8266 sketch"
```

## Configuration

`config.json` → `Config.TINA`:

| Key | Default | Meaning |
|---|---|---|
| `Model` | `qwen3:4b` | Ollama model |
| `OllamaUrl` | `http://localhost:11434` | Ollama endpoint |
| `Port` | `5013` | her web UI |
| `ContextTokens` | `4096` | context window |
| `HindsightUrl` | `http://localhost:8888` | shared memory server (DREAM hosts it) |
| `MemoryBank` | `tina` | her bank in Hindsight |
| `Roots` | `[".."]` | the repos she works in |

## Part of the DREAM Robotics fleet

DREAM · RIFT · NINA · NORA · MILA · WHIP · IDA · KIDA · ARM · LYCEA

---

*Formerly VERA, then TARA.*
