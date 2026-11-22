# Research writing, evidence review and rendering

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | Python package initialization. |
| [cli.py](cli.py) | writer CLI: agent task packets, validation, HTML/PDF rendering, and a fallback. |
| [figures.py](figures.py) | Fetch figures for a selected paper, with a deterministic generated fallback. |
| [model.py](model.py) | writer: selection -> push + knowledge-update proposals. |
| [render.py](render.py) | Render a long-form push (HTML fragment + assets) into a PDF. |
| [review.py](review.py) | Review task: write a proposition review (four sections) from its evidence. |
| [tasks.py](tasks.py) | Agent-facing writer tasks: build a writing packet, validate the pushes. |

## Subdirectories

- [templates](templates/AGENTS.md) — Report and push rendering templates.

## Verification

Start with [cli.py](cli.py) to inspect research writing, evidence review and rendering.
