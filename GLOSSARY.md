# Horizon（地平线）

产品方向见 [产品决策](docs/adr/0001-product-direction.md)。本表定义领域术语；包含 Base、Drive、Console 和旧队列字段的条目仅描述现有实现。

## Canonical product names

| English | 中文 | Meaning |
|---|---|---|
| Horizon | 地平线 | The software |
| Library | 图书馆 | Hpedia, Bulletin Board and Archive managed through the Librarian |
| Term | 术语 | One vocabulary entry and its definition, corresponding to one existing Knowledge Card |
| Hpedia | 百科 | The collection of Terms, Questions and Frontiers |
| Hpedia entry | 百科条目 | A Term, Question or Frontier with its own identity in Hpedia |
| Hpedia ID | 百科 ID | The permanent identifier of an Hpedia entry, with a type prefix and a hexadecimal body. References use the same ID across workspaces. |
| Question | 问题 | A specific question and its answer, together referencing at least one Term |
| Status (Term or Question) | 状态 | Whether the current definition or answer is considered valid (有效) or questionable (存疑) |
| Followed (Hpedia entry) | 追踪中 | Whether the Explorer is tracking the entry on the Bulletin Board. Questionable entries are always followed. |
| Bulletin Board | 白板 | Links to all followed Hpedia entries |
| Frontier | 前沿 | An open-ended research question with zero or more Updates, a tracking choice and a record of resolution when solved |
| Update | 进展 | A distinct development under a Frontier |
| Archive | 资料库 | The collection of original source records and their references to source documents |
| Retriever | 检索器 | The configurable collection of information sources available for Agent retrieval tasks. Formerly called Voyager. |
| Information source | 信息源 | An independently configurable member of the Retriever that accepts a search expression and returns results. Its enabled state determines its inclusion in manual and automatic retrieval when the Explorer has not specified sources in the conversation. |
| Explorer | 探索者 | A person using Horizon |
| Librarian | 图书管理员 | The module that mediates Library access, reviews proposed changes and applies changes the Explorer confirms |
| Confirmation card | 确认卡 | A related group of proposed knowledge changes that the Explorer reviews together |

Use these names in product copy, documentation and Agent explanations. Explorer names the person, not an Agent or Windows File Explorer. Technical protocol roles such as `user` and existing identity fields such as `owner` keep their technical meanings.

Existing `proposition` / `propositions` fields, IDs, commands and filenames represent Theme for compatibility; they are not a separate domain entity. Existing initial-build `theme` fields and paths represent temporary initial-build groups, not the canonical Theme entity. This terminology decision does not migrate stored data or change interfaces.

## Language

### Existing knowledge model

The terms below describe the existing implementation. See [Library and Librarian](docs/adr/0002-library-and-librarian.md) for the approved model.

**Knowledge Card（知识卡片）**:
The canonical unit of knowledge interest, defined by a label, recall terms and a precision annotation. It replaces the retired checkpoint.
_Avoid_: checkpoint, topic, keyword

**checkpoint**:
Retired name for a Knowledge Card id; it is not an independent entity.
_Avoid_: entity, node

**Under review card（待复核卡片）**:
A Knowledge Card or Theme the Explorer kept during review but excluded from retrieval, ranking and writing until the Explorer clears its review flag.
_Avoid_: draft, pending card, disabled card

**Theme（命题）**:
A judgment with conditions for when it applies, optional related Knowledge Cards and directional evidence.
_Avoid_: finding, summary, note

**Evidence（证据）**:
An entry that supports or opposes a Theme, with a reason and a source citation.
_Avoid_: citation, quote, source

**Source citation（来源引用）**:
A reference to an immutable material snapshot with an excerpt and the page or paragraph that can be opened.
_Avoid_: Report

**Report（报道）**:
A personalized explanation produced for the Explorer. Legacy paper records used report fields to store source references.
_Avoid_: paper, papers table, excerpt

**Review（综述）**:
A Theme's current synthesis, rewritten only from its evidence after accepted updates.
_Avoid_: article, summary, write-up

**Initial-build group（初始构建分组）**:
A temporary grouping used only to confirm the first batch of Knowledge Cards; the group itself is not stored.
_Avoid_: category, tag, folder

**Knowledge Base（知识库）**:
Horizon-managed knowledge representing what the Explorer has mastered, with automatic and manual Knowledge Card and Theme updates. Source collection alone does not establish mastery.

**Legacy Frontier（关注问题）**:
The Explorer's focus question with an explicit research scope, hard constraints, a version and a following status; it is the durable knowledge scope a Voyager retrieval plan can use. Relations stay empty in K1.
_Avoid_: topic, saved search, subscription

**Legacy Frontier status（旧关注状态）**:
The lifecycle state of a Frontier: following, paused, resolved or deleted. Pause, resolve and delete stop future retrieval without destroying the Frontier's history or already-produced materials; deleted is terminal and cannot be edited.
_Avoid_: enabled flag, archived, closed

**Retrieval plan（检索计划）**:
A Voyager request created from a temporary explicit scope or a Frontier, recording its scope snapshot, queries, window and budgets. Creating a plan does not contact a source.
_Avoid_: search, retrieval run, result

**Retrieval run（检索执行）**:
The explicit Voyager execution that accesses configured sources under a plan's budgets and records candidates, cursors and gaps. It is distinct from creating the plan and independent of model configuration.
_Avoid_: plan, scheduled job

**Scope snapshot（范围快照）**:
The scope values a Voyager request keeps from creation, including the Frontier id and version when the plan came from a Frontier. Later Frontier edits do not rewrite it.
_Avoid_: live scope, current scope

**Personalized explanation（个性化解读）**:
Writing grounded in new sources, existing knowledge and accumulated Explorer preferences; independent of delivery.

**Negotiation（谈判）**:
Discussion that corrects interpretations, expresses preferences and revises proposed knowledge updates; distinct from a rating.

### Delivery

**Push（推送）**:
A delivered personalized explanation with feedback and knowledge-update discussion.
_Avoid_: article, PDF, post

**Push artifact（推送件）**:
The self-contained HTML rendered for one push; it records what the Explorer saw and is not a regenerable view.
_Avoid_: push, PDF, report file

**Rating（评分）**:
The Explorer's `irrelevant` / `ok` / `great` verdict on a push; the existing queue uses it to gate update questions; the product also uses accumulated preferences to guide explanations.
_Avoid_: score, vote, feedback score

**Unified question queue（统一问题队列）**:
One serial queue of rating and update questions, with one active question and at most two entity targets per push.
_Avoid_: approval queue, inbox, pending list

**Pending retrieval request（待推请求）**:
A retrieval trigger recorded while the question queue is non-empty; requests coalesce and run after the queue empties.
_Avoid_: backlog, retry queue, scheduled job

**Writer**:
The agent role that proposes Knowledge Card and Theme updates; it never writes the archive directly.
_Avoid_: author, editor, generator

### Installation and storage

**Installation workspace（安装工作区）**:
The host project folder that contains the code directory and the archive directory as separate children.
_Avoid_: repo root, project root, workspace root

**Host（运行宿主）**:
The agent application that installs horizon on the Explorer's machine and performs the judgement work the Console asks for; it neither owns scheduling nor provides liveness.
_Avoid_: machine, runtime, bot

**Host schedule（宿主定时任务）**:
Retired. Planned runs are scheduled and started by the Console; the host's own schedule starts no horizon run.
_Avoid_: cron job, service, daemon

**Listener（监听进程）**:
Retired. Its Feishu event consumption, feedback polling and agent-run spawning now belong to the Console; its module, subcommand and start/stop scripts were removed in spec #74.
_Avoid_: service, daemon, bot, background job

**Console launcher（控制台启动脚本）**:
The legacy script entry used to start the Console in the existing distribution and source workflows.
_Avoid_: listener script, service, shortcut

**Horizon application（Horizon 应用）**:
The application providing source and knowledge management, explanations, delivery, ratings and negotiation.
_Avoid_: installer, host, installation workspace

**Heartbeat（心跳）**:
The Console's periodic liveness record, rewritten while the Console runs; the evidence used by other horizon commands to tell "running" from "process exists".
_Avoid_: pid file, ping

**Listener health check（监听巡检）**:
Retired. Liveness is visible in the Console itself; no scheduled probe exists in the code and nothing restarts anything.
_Avoid_: watchdog, monitor, uptime check

**Derived host run（派生运行）**:
Retired name for an Agent run.
_Avoid_: headless agent, child session, bot run

### Distribution and release

**Horizon installer（Horizon 安装器）**:
The downloadable Windows setup application that lets the Explorer select where to install the Horizon application.
_Avoid_: distribution package, Console launcher

**Distribution package（发行包）**:
The self-contained artifact an Explorer installs horizon from: program code, agent-facing assets and the runtime it needs, identified by one version.
_Avoid_: installer, build, release, bundle

**Distribution manifest（发行包清单）**:
The in-package index of every file's digest together with the package's version and compatibility facts; it is the evidence of what an installation contains.
_Avoid_: checksums, file list, lockfile

**Install lock（安装锁）**:
The installation's pinned record of which distribution package it runs: version, artifact digest, channel, source and install time.
_Avoid_: version file, install record

**Release channel（发布渠道）**:
The line a distribution package is published on — stable or prerelease — and the only thing a test installation selects by default.
_Avoid_: branch, tag, market

**Source installation（源码安装）**:
An installation run from a developer's source checkout, used for development rather than for Explorers.
_Avoid_: dev install, editable install

### Runtime control

**Console（控制台）**:
The single foreground program that owns horizon service on a host: it schedules planned runs, consumes Feishu input, starts bounded agent runs on the Explorer's behalf, and shows run information. Closing it stops the service; it is not the operating system's service.
_Avoid_: listener, service, daemon, bot, TUI

**Console command（控制台指令）**:
A slash-prefixed text typed in the Console that names one operation from the operation whitelist.
_Avoid_: CLI command, keyword

**Operation whitelist（操作白名单）**:
The closed set of horizon operations an agent run may perform on the Explorer's behalf. It excludes any change to horizon's own definition.
_Avoid_: command list, permissions

**Instruction whitelist（指令白名单）**:
The natural-language and slash phrases that map directly to one operation; a message matching one runs that operation without an intent judgement.
_Avoid_: aliases, keywords

**Intent judgement（意图判定）**:
The agent run that maps a message outside the instruction whitelist to at most one operation from the operation whitelist. It never executes the operation; the Explorer confirms it first.
_Avoid_: classifier, NLU

**Agent run（agent 运行）**:
A bounded run of the built-in Pi Agent that the Console starts for work requiring judgement, such as sorting and writing one push. It holds the same single-executor lease and is not a second executor.
_Avoid_: headless agent, child session, bot run

**Code directory（代码目录）**:
The horizon program directory — a source checkout in a source installation, an extracted distribution package in a normal installation — that also holds the installation's environment, configuration, run state, queue, logs and archive index; it is kept separate from source documents and push artifacts.
_Avoid_: workspace, install directory, app folder

**Data root（数据根）**:
Retired. The location it named split in two: the Code directory now holds configuration and run state, and the Archive directory holds the source files.
_Avoid_: cache, backup, home

**Archive directory（资料目录）**:
The local location for a paper's source document and the pushed artifacts, and nothing else; every other file it used to hold belongs to the Code directory.
_Avoid_: data root, cache, backup

**Archive index（资料登记）**:
The Code-directory ledger that registers each file in the Archive directory by business id and md5, so the archive holds no ledger of its own.
_Avoid_: manifest, catalog, database

**Private environment（私有环境）**:
The uv-managed Python environment and interpreter kept inside the Code directory of a source installation, created by a uv resolved from outside the workspace first; later runs use the recorded absolute interpreter.
_Avoid_: repo venv, workspace virtualenv, system Python

**Bundled runtime（内置运行时）**:
The interpreter and libraries a distribution package carries, so an installation needs neither a toolchain nor a package manager on the Explorer's machine.
_Avoid_: venv, embedded Python, private environment

**Source document（原文）**:
An original source used for interpretation, including papers, news, posts and blogs. The existing PDF adapter keys its source file by `paper_id`.
_Avoid_: literature, reference, attachment

**Legacy storage library（旧版存储库）**:
A Drive root directory plus its Base registration table; source and push libraries share one quota.
_Avoid_: folder, bucket, archive

**In library（在库）**:
A file is in Drive and registered in the Base; only this state counts toward the quota.
_Avoid_: uploaded, synced, stored

**No source file（无原件）**:
A source record exists but Drive has no file, usually because of a paywall.
_Avoid_: missing paper, broken record, empty

**Archived offline（已离线归档）**:
The file was downloaded, md5-verified and removed from Drive; its Base row remains permanently.
_Avoid_: deleted, purged, removed

**Pending upload（待上传）**:
A Base row exists without a Drive file and is waiting for the file.
_Avoid_: missing, orphan, queued

**Quota（配额）**:
The combined Drive bytes allowed for the source and push libraries, defaulting to 5 GB.
_Avoid_: limit, capacity, storage cap

**Cache（缓存）**:
Locally downloaded or derived copies that can be deleted and rebuilt, validated by md5.
_Avoid_: mirror, backup, local library

**Offline backup（离线备份）**:
A verified local copy made before removing a file from Drive; it may be the only remaining copy.
_Avoid_: export, archive, snapshot

**Reconcile（对账）**:
Comparing Drive files with Base registrations to find orphan files, broken links, duplicates and state mismatches.
_Avoid_: sync, audit, verify

**Decision（决定）**:
The Explorer's accepted or rejected outcome for one update proposal, recorded per proposal.
_Avoid_: approval, answer, vote

**Part（分片）**:
An ordered fragment of a source file that exceeds the Drive per-file limit.
_Avoid_: chunk, segment, piece
