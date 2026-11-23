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

`Horizon.cmd` remains the CLI entry. Without arguments it also starts the GUI, but may briefly show a console. Chat with the embedded Agent in the center, switch conversations on the left, and configure the model using Settings. Use the standalone CLI Console for legacy commands. Enter sends; Shift+Enter inserts a newline. Stop cancels the active Agent. Conversations stay in `%LOCALAPPDATA%/Horizon/data/horizon.sqlite3`. Set `HORIZON_DATA_HOME` to an absolute path to choose another data directory; changing installation directories preserves the same data home. Existing workspaces are not imported.

On first launch, the bundled Electron runtime is copied to `%LOCALAPPDATA%/Horizon/desktop-runtime/` to avoid Windows sandbox startup failures on workspace disks. Code, configuration and archives stay in their original locations; the renderer sandbox stays enabled. Closing the window keeps the application running in the tray. Use Exit Horizon to stop its workers.

Open Automatic tasks to create, edit, pause or resume a plan. Supply an explicit task scope and interval; these development scopes are separate from Knowledge Base Frontiers. The default execution limits are two main tasks and four child agents, adjustable in the same panel. Work from one plan runs serially, and paused work blocks that plan until continued or abandoned. Missed triggers merge into one run; declared execution coverage is retained across restarts. It is not proof of source retrieval. Missing or invalid coverage declarations hold the run for review; abandon that run to pause the plan without advancing its covered range.

The Trace tab is currently empty. A completed response or rewrite does not authorize sending. When a delivery channel is configured, explicitly prepare and send a completed result. Uncertain delivery requires a lookup first; sending again requires acknowledging the risk of duplicate delivery. An unconfigured channel shows its status and does not send.

Open **Materials** to paste text, give it a title and source label, and register it offline without model credentials. Select a saved material to read its exact original text and source identity, receipt time, SHA-256 and extraction version. Generated summaries appear separately; registration does not generate one or contact a remote service. Originals remain available independently of conversations.

In **Materials**, choose **导入 PDF** to import a local PDF up to 10 MiB. Select a page number to compare extracted text against the original page, especially for columns, tables and equations. When text is unavailable you can supply it yourself or abandon the request.

In **Materials**, enter a public HTTPS URL and select **获取网页正文**. The saved material shows the source link, acquisition time and numbered paragraphs from the saved snapshot. You can reopen it offline. An access failure keeps the link and reason available for review.

Use **归档资料** to move a material into **已归档资料** while retaining its snapshots and citations. Before permanent deletion, review the references and retrievals listed under **查看永久删除影响**, then confirm. **回收无引用快照** reclaims blobs that are no longer used by any surviving version or citation.

For a page that renders its public article with JavaScript, select **使用浏览器渲染公开动态页** before capture. Voyager candidates also have a browser capture action. Dynamic capture uses the Chromium bundled in the Windows package.

### Desktop development

Choose Exit Horizon before running a newer setup installer. The installer uses its own bundled runtime, backs up the existing data home, and keeps your conversations in the same location. It refuses to replace an active desktop installation.

Run `npm ci`, `npm run typecheck` and `npm run build` in `desktop/`, then open the shortcut or run `python -m hostrun.desktop --code <code> --archive <archive>`. Python owns a token-authenticated loopback bridge and the Windows process job. Startup errors are recorded in `.horizon/desktop-error.log`; Electron logs are in `<data-home>/desktop-electron.log`.

Desktop conversations retain their model context when you continue a completed task or reopen Horizon. Start a new conversation for separate context. Configure your model in Horizon settings; credentials remain in the backend data home.

Model setup: choose a provider and enter its API key. Horizon queries the provider for models visible to that credential; choose a model in the lower-right conversation menu. Refresh reloads the list without re-entering the key. An advertised model may still require account permissions or quota. Custom OpenAI-compatible services additionally require their API base URL.

The built-in Agent can delegate bounded development-tool tasks to independent child sessions. After running an explicit Voyager retrieval attached to the current task, select **在对话中整理 Voyager 资料包** and send the prepared message. The Voyager child summarizes candidate metadata, captured snapshot references and coverage gaps. Open the referenced materials to read the source text and verify claims.

Desktop conversations recover unfinished turns after an unexpected process exit. Committed child results are reused. This development stage exposes read-only and restricted child tools for recoverable turns; legacy mutating workflows are not automatically replayed.

Closing the window keeps Horizon running in the tray. Choose Exit Horizon to stop.

Temporary model failures use bounded retries.

Use **Export diagnostics** in the desktop header to create a local report folder. It contains bounded lifecycle/task/error metadata, versions and task-state summaries. It excludes API keys, configuration files, conversation and document text, databases and Pi transcripts; nothing is uploaded automatically. Reports are stored under the data home’s `diagnostics/reports/` directory. If the GUI cannot start, the installed runtime can export with `runtime/python.exe -m hostrun.diagnostics --code <installation> --output <report-directory>`.

### Knowledge workbench

Open **知识库** in the desktop app. The left pane searches local knowledge and the right pane opens the selected record for reading, editing, or inspecting evidence. Search accepts single Chinese characters, longer Chinese phrases, and mixed Chinese and English. Full-width Latin letters and case match their ordinary forms. An empty search lists the selected set.

**当前有效** shows active Knowledge Cards and Themes and following Frontiers. Use the type filter to narrow the list, and the related-record filter to find records directly linked to a specific card or Theme. Choose **归档与停用** for archived Cards and Themes or paused, resolved, and deleted Frontiers. **待确认** is a separate view. Select a Theme and use **打开来源** in its evidence to open the cited portion of the saved snapshot without leaving the workbench.

Select a record and open **版本历史** to see what changed, who changed it, when, and any reason supplied. To undo an edit, open its preview and check the proposed changes against the current version before confirming. An undo creates a new version, preserves earlier versions and leaves later changes to unrelated fields in place. If a later edit changed the same field, the preview shows the conflict and Horizon leaves the record as it is. History remains readable for archived or deleted records. The workbench uses local data and requires no Feishu account.

Open **知识库 → 待确认** to review Keeper proposals. Each card groups related changes and shows the affected objects, before and after values, reasons, saved-source excerpts and the linked report version. Choose **批准整组** to apply all changes together, or **拒绝** to decline them. You can include a reason for either decision. If a target was edited after the card was prepared, Keeper checks the latest knowledge in the original task. The group remains unapplied. Compare the revised card with the earlier plan and approve the new revision when ready.

Under **要求调整**, choose which proposed fields to leave at their current values or which changes to remove. Explain your request in the feedback field. Keeper receives the feedback on the original task and reviews the remaining changes. A revised plan needs its own approval. Recorded decisions remain available after restarting the app. Proposals labeled **测试请求** come from an explicit development test request.

For a proposed Card merge, check the Card that will remain active, the content and sources it will retain, and the relationship changes before approving the group. A Theme proposal can keep both supporting and opposing evidence and explain where the judgement applies. To reverse an approved proposal, open it under **已批准** and choose **预览整组撤销**. Check the current versions and the proposed restoration before confirming. If anyone has since edited a target, the preview shows the differences and the group stays unchanged.

### Knowledge Base Frontiers

Open **知识库** to create a Frontier: a focus question, original research scope, bilingual alternative queries, required and excluded terms, source identifiers, and an initial status of **关注中** (following), **已暂停** (paused) or **已解决** (resolved). Relations stay empty in this stage; an empty list is valid. Creating or editing a Frontier commits directly, without a Keeper approval step.

**编辑（读取完整版本）** loads the full committed Frontier, not the truncated list summary. The form stays pinned to the version it loaded: if the Frontier changes elsewhere, saving reports a version conflict and keeps the draft for review. A draft is cleared only after the matching committed save succeeds; errors keep it. Deleted Frontiers cannot be edited. Use the status field or the explicit list controls to pause, resolve, re-follow or delete. Pausing, resolving and deleting stop future retrieval without destroying earlier retrieval records or materials.

**创建检索计划（不检索）** creates a Voyager plan for a following Frontier using its committed version and, when available, the current conversation. It does not contact a source. Open the plan under **范围检索** and choose **开始实际检索（访问来源）** to run it. Retrieval is independent of model configuration. Voyager details show the scope type and, for a Frontier plan, the Frontier version; temporary explicit scopes keep their previous behavior.

### Knowledge Cards

Open **知识库 → 知识卡片** and enter a title and content. Add aliases, recall terms or related cards as needed. To cite material, select a saved item, enter a locator and copy an excerpt from its text. Horizon records the source snapshot and version. A card without a verified citation from captured public web or academic material shows **用户陈述 · 来源待补充**. Mark uncertainty with **存疑**.

**编辑（读取完整版本）** pins the draft to a committed version. If another edit was saved first, the panel keeps your draft and shows its differences from the latest version. Review the changes before reapplying the draft or discarding it. Archived cards stay readable and leave the active known set. Reopening Horizon restores committed cards and their versions. Reading and rating leave knowledge unchanged.

### Knowledge Themes

In **知识库**, select **命题** or create a new one to record a judgement and its applicability conditions. Add related cards and evidence as needed. Each supporting or opposing entry records a reason, a saved material, a locator and an excerpt. Themes with one direction or no evidence show **证据不足**. Adding a source leaves the judgement and earlier evidence as recorded.

Choose **打开来源** to read the pinned page or paragraph. Use an exact locator such as **第 2 页** or **p6** when the same excerpt appears in multiple places. Archived Themes remain readable and leave the active known set.

### Explicit retrieval scopes

Open **范围检索** to preserve an original research scope, bilingual alternative queries, required/excluded terms and source restrictions. Choose an initial foundation/recent search or an incremental overlapping window, with publication or discovery time as the basis. Defaults are 900 seconds, 20 queries, 100 candidates and 20 full texts per execution segment; reaching any limit retains partial results and gaps. Continue grants a new segment while preserving cumulative use and uncertain prior work. Successful query cursors are shown separately from gaps. Temporary scopes do not create Frontiers; a Frontier plan records the Frontier and its version instead. Creating a plan only stores it; **开始实际检索（访问来源）** runs source access. Enter `arxiv` or `europepmc` in the source list to search arXiv or Europe PMC. Both are built in and require an internet connection. If one source fails, inspect its gap alongside the results from other sources. Use `crossref` for anonymous Crossref searches and `openalex` with an API key from a verified OpenAlex account. Save the key under **范围检索 → 检索来源 API**. OpenAlex checks the account’s remaining daily budget before searching. Each candidate retains its source ID, DOI when supplied, and up to 100 reference identifiers. Counts come from the source and may exceed the listed references.

The candidate list shows matched scope terms, possible counterevidence, corrections and retractions, and links back to the queries. Multiple sources with the same material identity are grouped with their provenance. Republishing disclosures are marked as provenance. A fully covered run with no qualified new items shows **无新增**. Partial runs and failures retain the gaps alongside any available candidates.

For optional web discovery, select source `tavily` and save your own key under **范围检索 → 检索来源 API**. Use a free Researcher plan with PAYG disabled and a finite Monthly Limit for the key. Horizon verifies the plan and uses Search basic. The settings show whether a key is saved. Authorization and quota are checked when you search. Launch environment keys (`OPENALEX_API_KEY` and `TAVILY_API_KEY`) are used when there is no saved key. Missing credentials or unknown/exhausted credits pause this channel. Select discovery time and set the end of the window after the run to include Tavily results. Search snippets remain metadata, not full text.

If Tavily reports a null PAYG or key limit, open **Tavily 免费预算**, confirm PAYG is disabled in the Tavily console and enter the key's Monthly Limit. Horizon still checks the remaining free credits and stops if Tavily reports PAYG usage or a positive PAYG limit. Changing the saved key clears the confirmation.

In **范围检索 → 订阅更新**, add a public HTTPS RSS/Atom feed or a section URL, then select **刷新订阅**. A successful round shows the new or changed entries and the last success time. The panel shows up to ten entries from the latest round. To search saved versions, copy the displayed `sub_…` source identifier into the scope and select discovery time. Section discovery stays within the supplied host and path and follows at most two next-page links per round.

Select **获取候选开放全文** to look for an academic candidate’s original PDF,
HTML or XML. Horizon checks source-native documents, open-access DOI locators,
and the public landing page. To enable Unpaywall, save a contact email under
**范围检索 → 检索来源 API**. **查看资料与获取记录** opens the saved snapshot and
its provenance or shows why the material is still waiting for full text.

For an academic item awaiting full text, open its source record in **Materials** to supplement it with a readable PDF, abandon it, or explicitly authorize abstract-only analysis for its original task. The abstract carries a full-text limitation. A late supplement from a scheduled task waits in the same plan's queue. It neither changes the covered interval nor resumes a paused plan.
