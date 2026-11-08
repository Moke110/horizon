# Horizon（地平线）

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Moke110/horizon?include_prereleases&label=release)](https://github.com/Moke110/horizon/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078D6)](#what-you-need)
[![Runtime](https://img.shields.io/badge/python-3.13%20bundled-3776AB)](#what-you-need)

**English** | [简体中文](README.zh-CN.md)

**A source discovery, knowledge management and personalized explanation assistant for people who keep learning across industries.**

Horizon (地平线) calls each person using the software an **Explorer (探索者)**. Its **Knowledge Base (知识库)** contains **Knowledge Cards (知识卡片)** and **Themes (命题)**.

Horizon has four parts: retrieving original sources (papers, news, posts and blogs); managing its own Knowledge Base with automatic and manual Knowledge Card and Theme updates; writing explanations grounded in what the Explorer already knows and their accumulated preferences; and an Agent that schedules retrieval, writing and delivery.

The Horizon GUI provides reading, ratings and negotiation about interpretations, preferences and knowledge updates. Feishu is an optional external interaction and delivery channel. Collecting a source does not mean the Explorer has mastered its knowledge.

The confirmed [product decision](docs/adr/0001-product-direction.md) defines the direction. The following commands and setup notes describe the existing implementation, including its Feishu adapter; they do not claim that the new knowledge suite and interaction flow are already implemented.

## Installation

Horizon includes its own Pi-based Agent. You need Windows 10/11 x64, a model API key,
and a Feishu account authorized to create and use the required app, Base and Drive resources.
The distribution includes Node.js, Pi, the Feishu CLI and the Python business runtime.
No separate DSH or Pi installation is needed.

Extract a Horizon distribution into a writable directory and open PowerShell there:

```powershell
.\bin\horizon.cmd console install
.\bin\horizon-console.cmd
```

Run `/horizon-init`: choose an archive folder, enter your model provider, model name and API key,
then complete Feishu authorization, resource setup and daily push time. For an OpenAI-compatible
service, also enter its API base URL. Model/provider names must match your service.
The key is stored locally in `.horizon/agent/model.json`; protect this file. Installation reports
and process arguments do not include it.

Configuration, queues and logs live in `.horizon/`. The separate archive contains `originals/`
and `pushes/`. Interrupted setup resumes. Existing DSH installations must complete the new
model configuration step. No external host skill registration is required.

Use `install package --from <zip> --digest <digest.json>` for verified package upgrades,
`install verify` to check files, and `install rollback` to restore a retained version.
Upgrades preserve `.horizon/` and the archive.

## Using horizon

Use the console or Feishu:

- **Console** (existing command interface): the foreground window shows scheduled runs,
  Feishu messages and card clicks, agent-run summaries and errors; the input line at the
  bottom takes the commands below.
- **Feishu direct message** while the console is open: send one of the six phrases below.

| Console command | What it does |
|---|---|
| `/horizon-init` | first-run setup; also resumes an interrupted setup |
| `/horizon-start` | start the service again after a pause |
| `/horizon-stop` | pause pushes and Feishu replies; the console stays open |
| `/horizon-status` | current state, last run, last failure |
| `/horizon-push` | request one more report now |
| `/horizon-update-kb` | incremental knowledge-base initialization with per-item review |
| `/horizon-logs` | recent console and agent-run logs |
| `/horizon-help` | command help |
| `/exit` | stop the console (and with it the whole service) |

| Feishu direct message | Meaning |
|---|---|
| `推一篇` | push one report now |
| `处理反馈` | process submitted feedback |
| `状态` | current status |
| `暂停` | pause the service |
| `恢复` | resume the service |
| `测试飞书消息` | send a test message to yourself |

Only your own direct messages are accepted; groups and other people are ignored. While the
service is paused, only `恢复` gets through and every other message is answered with a pause
notice. Two commands need arguments — changing the push time and changing the archive
directory — so horizon first runs a read-only intent check, and only a confirmation card
you click executes the change.

Each report arrives with a feedback entry point. Rate it first, then confirm each knowledge
update question one by one; an edited answer only takes effect after you explicitly submit
it. Unanswered questions block the next push, but they do not block storage maintenance.

The console keeps time itself: one push per day at your chosen time (default 08:00), with a
single catch-up run if a day was missed. Feedback is polled every five minutes, and every
ten minutes horizon pulls text edits and "under review" flags from the Base back into the
local copy of truth. It stays quiet when nothing changed.

### Initializing or extending the Knowledge Base

`/horizon-update-kb` pauses the formal service, asks you for a folder, has the agent read
everything in shards, and prints each candidate Knowledge Card and Theme for review: press Enter to
accept, Backspace to discard, Tab to keep it marked `Under review`. Knowledge Cards and Themes carrying
that marker are not retrieved or written about until you clear "under review" in the Feishu
Base. Progress is incremental: a second run reads only files the archive has not registered
yet.

## Pausing, moving to another machine and recovering

`/horizon-stop` (or `暂停` in Feishu) pauses scheduled pushes and Feishu replies while the
console stays open; `/horizon-start` (or `恢复`) resumes. Closing the console window stops
the whole service and nothing responds in Feishu until you open it again. When you reopen it,
horizon restores the previous state: paused stays paused, running starts the service again.

To move to another machine or host, stop the console on the old machine first, then run the
installation on the new one and choose to connect to the existing Feishu Knowledge Base; the
agent takes over and restores from there. Only one executor may use a Knowledge Base at a
time.

The local copy is rebuildable, but an offline archive can be the only remaining copy of a
paper that was removed from Drive — keep it. Uninstalling the runtime configuration never
deletes Feishu data, and deleting cloud originals always needs a separate confirmation after
a verified offline backup.

## Troubleshooting

- **The console exits right after starting** — usually missing write permissions or a damaged
  runtime. Relaunch the launcher with full permissions, or inspect the
  conclusions of `install report` and `install verify`.
- **Feishu commands do nothing** — the console must be open. Closing the window stops all
  Feishu responses; queued feedback is processed after the next start.
- **The queue does not move** — check that answers were explicitly submitted and that there
  is no older unanswered question blocking the next push.
- **A push failed** — `/horizon-status` shows the last failure and the number of attempts.
  horizon does not retry automatically: fix the cause, then request a new push.
- **"Running" does not mean delivered** — a scheduled run that produced no delivery evidence
  is reported as such. Trust the delivery result, not the timer.

## Development (source install)

Use Python 3.13 and Node.js >=22.19.0. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
npm --prefix agent ci --ignore-scripts
npm --prefix agent run build
node agent/node_modules/@larksuite/cli/scripts/install.js
.\.venv\Scripts\python.exe -m hostrun.cli console install
.\bin\horizon-console.cmd
```

The TypeScript agent uses the Pi SDK and explicit tools to call existing Python business modules.
Python retains validation, storage and PDF processing. Check the agent with
`node agent/dist/main.js --check`; run regression tests with `python -X utf8 -m pytest -q`.
Release builds require the compiled agent and both bundled runtimes.


## Docs and license

- Built-in agent workflow: [skills/horizon-run/SKILL.md](skills/horizon-run/SKILL.md)
- Development guide: [AGENTS.md](AGENTS.md) · Glossary: [GLOSSARY.md](GLOSSARY.md)
- Module contracts: [contracts/](contracts/README.md)
- License: [MIT](LICENSE)

## Desktop interface

Double-click the `Horizon.lnk` shortcut for everyday startup. The extension starts with a lowercase L; Windows usually hides it. Run `scripts/install-desktop.ps1` to regenerate it; add `-ShortcutDirectory <folder>` to also create a “启动 Horizon” shortcut in that folder. Source startup requires Python and an installed, built `desktop/`; Tkinter is not required.

`Horizon.cmd` remains the CLI entry. Without arguments it also starts the GUI, but may briefly show a console. Chat with the embedded Agent in the center, switch conversations on the left, and configure the model using Settings. The desktop Console tab is inactive; use the standalone CLI Console for legacy commands. Enter sends; Shift+Enter inserts a newline. Stop cancels the active Agent. Conversations stay in `%LOCALAPPDATA%/Horizon/data/horizon.sqlite3`. Set `HORIZON_DATA_HOME` to an absolute path to choose another data directory; changing installation directories preserves the same data home. Existing workspaces are not imported.

On first launch, the bundled Electron runtime is copied to `%LOCALAPPDATA%/Horizon/desktop-runtime/` to avoid Windows sandbox startup failures on workspace disks. Code, configuration and archives stay in their original locations; the renderer sandbox stays enabled. Closing the GUI stops its background workers within 10 seconds.

### Desktop development

Run `npm ci`, `npm run typecheck` and `npm run build` in `desktop/`, then open the shortcut or run `python -m hostrun.desktop --code <code> --archive <archive>`. Python owns a token-authenticated loopback bridge and the Windows process job. Startup errors are recorded in `.horizon/desktop-error.log`; Electron logs are in `<data-home>/desktop-electron.log`.

Desktop conversations retain their model context when you continue a completed task or reopen Horizon. Start a new conversation for separate context. Configure your model in Horizon settings; credentials remain in the backend data home.

Model setup: choose a provider and enter its API key. Horizon queries the provider for models visible to that credential; choose a model in the lower-right conversation menu. Refresh reloads the list without re-entering the key. An advertised model may still require account permissions or quota. Custom OpenAI-compatible services additionally require their API base URL.

The built-in Agent can delegate bounded development-tool tasks to independent child sessions. The Trace tab shows their role, parent/child session identities, input version, stable result reference and success or failure. These initial read-only development roles are not Voyager, Editor or Keeper.

Desktop conversations recover unfinished turns after an unexpected process exit. Committed child results are reused. This development stage exposes read-only and restricted child tools for recoverable turns; legacy mutating workflows are not automatically replayed. The Trace tab separates execution from material waiting, knowledge proposals and delivery.
