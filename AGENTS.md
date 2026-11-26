# horizon

This file contains development-agent working instructions only. Keep product positioning and feature explanations out of this file. Read `docs/adr/` for confirmed decisions and `GLOSSARY.md` for terminology.

## Development responsibilities

- **Planning:** Use the Matt Pocock workflow: `wayfinder` to map uncertain work, `grill-with-docs` to resolve decisions and record domain docs, `to-spec` to publish the agreed spec, then `to-tickets` to publish approved vertical slices and native blocking links. The skill is named `to-tickets` (plural).
- **Implementation:** Execute the approved GitHub spec and tickets. Read the issue body, comments and blockers before starting; work only on unblocked tickets, and follow the skill's testing, review and commit requirements. Record results and remaining blockers in GitHub; close a ticket only when its acceptance criteria are met. Offline tests do not replace required live acceptance.
- **Handoff:** Planning ends with linked GitHub issues and clear acceptance criteria/dependencies. Implementation starts when the user requests it; a planning request alone does not authorize implementation. Return changes to scope or unresolved product decisions to the planner rather than silently changing the spec.
- **WorkBuddy:** no longer used to develop this repository. Keep project-specific WorkBuddy configuration and memory out of the workspace. This development-role change does not remove WorkBuddy as a horizon runtime host or waive its product acceptance requirements.

## Human acceptance

Complete automated checks and prepare the reviewable result before requesting manual acceptance whenever possible. After the user supplies the requested confirmation, automatically continue the remaining authorized work; do not wait for another continue instruction.

## Git workflow

All repository work, including planning documents, implementation and fixes, is done on **`dev`**. **`main`** is the integration branch and the only branch that is pushed; keep the remote default branch as `main`.

1. Before editing, inspect the working tree and fetch `origin`. Switch to `dev`; create it from the current agreed baseline if absent. Preserve existing commits and user changes. If switching would overwrite work, resolve that state without discarding it.
2. Synchronize `dev` with `origin/dev` and `main` before starting. Prefer fast-forward updates; resolve any divergence on `dev` without rewriting published history. Use explicit branch names when pulling or merging.
3. Commit completed changes on `dev` using explicit file paths. Run checks appropriate to the change and review the diff. An implementation's required live acceptance must remain visibly pending if unavailable.
4. Once the intended change is complete and verified, fetch again, fast-forward local `main` to `origin/main`, and incorporate any new `main` changes into `dev`. Resolve and verify integration on `dev`; keep `main` free of direct development commits.
5. Merge `dev` into `main` with `git merge --no-ff dev`, then push `main` only: `git push origin main`. Never push `dev`; `origin/dev` may lag behind and is not a delivery branch. On rejection, fetch and reconcile without force-pushing.
6. Fast-forward local `dev` to the merged `main` (`git checkout dev && git merge --ff-only main`, or `git branch -f dev main` while `dev` is not checked out) so the next task starts from the same integrated baseline; do not push that fast-forward. Verify a clean working tree and matching local/remote `main` tips before reporting completion.

Do not force-push, reset away commits, or delete historical branches as routine cleanup. Explicit user instructions such as “documents only” or “do not push” constrain this workflow.

## Desktop GUI updates

Update the Windows GUI from the integrated source checkout. Run `npm --prefix desktop run build` and restart through `Horizon.cmd` using the existing workspace and data paths. GUI updates require no release tag, GitHub Release, or installation package.

## Agent skills

### Issue tracker

Issues, specs, tickets, and wayfinder maps live only in GitHub Issues of `Moke110/horizon`; do not create local spec/ticket drafts or mirrors. See `docs/agents/issue-tracker.md`. The retired `.scratch/` tracker is available through Git history, not the working tree.

### Triage labels

Default five-role vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout (`GLOSSARY.md` + `docs/adr/` at the repo root). See `docs/agents/domain.md`.

### Runtime and user documentation

`README.md` (English, primary) and `README.zh-CN.md` (Simplified Chinese) are the user-facing installation and usage guides; keep the two copies in step with each other, and keep development/acceptance status out of both — that belongs in `docs/acceptance/` and GitHub Issues. The product's host agent loads `skills/horizon-run/SKILL.md`; its installation and workflow references are runtime instructions, not development skills. Module contracts live in `contracts/`. Keep root documentation limited to `AGENTS.md`, `GLOSSARY.md`, `README.md` and `README.zh-CN.md`, with `LICENSE` retained at the root.

Keep `docs/adr/` for Matt-maintained decisions, `docs/agents/` for Matt skill configuration, and `docs/acceptance/README.md` for concise evidence and outstanding acceptance gaps. Put schemas/state machines in `contracts/`, sample/generated documentation beside its fixtures, and historical process logs in Git history rather than duplicate local trackers or reports.

For development checks, use an independent Python environment with PyYAML, pytest and PyMuPDF. The repository `.venv` is a uv-managed CPython 3.13 environment and passes the suite (1012 passed, 3 skipped on 2026-10-31); rebuild it with `uv` only if its interpreter disappears. On Windows, use `python -X utf8 -m pytest -q` to avoid locale-dependent reads without persistent environment changes. The release pipeline and the distribution package are documented in the "Release, tags and channels" section above; real acceptance evidence belongs in `docs/acceptance/README.md`.

Put relocatable temporary files for development, tests, packaging and acceptance under the checkout's `tmp/`. Use short, per-run paths and set tool options or process-scoped environment variables before launching tools so pytest (`--basetemp`), caches, build staging, diagnostic scratch files and GitHub `--body-file` transport stay there. When an acceptance requirement depends on the real platform install or data location, use that location only for the part under test and keep staging and other scratch files in `tmp/`.

Keep the checkout root free of ad hoc dot-prefixed directories. Use a root dot-directory only when an established project or tool convention requires that location (for example, `.git/`, `.github/`, `.horizon/` or `.venv/`).

Create linked Git worktrees under the primary checkout's `tmp/worktrees/<name>/`. Collect temporary test output under `tmp/tests/<run>/`, including pytest basetemp, caches, logs and generated fixtures. For example, pass `--basetemp=tmp/tests/<run>/base -o cache_dir=tmp/tests/<run>/cache` to pytest, substituting a short, unique run name.

### Skill pack

Read the relevant `SKILL.md` before applying a skill. Shared Matt Pocock skills are installed under `~/.agents/skills/<name>/`; source metadata lives in `~/.agents/.skill-lock.json`. Use the supported skill-loading mechanism against this shared source, without relying on WorkBuddy junctions. If a required skill is unavailable, report the missing integration rather than inventing its behavior.

The planning/implementation skills above are user-invoked workflows. Follow each skill's approval gates and delegated skills (including testing and code review); do not automatically start implementation merely because its skill is installed.

The per-repo configuration those skills read is `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` and `docs/agents/domain.md`. Edit those files directly; `setup-matt-pocock-skills` only needs re-running to switch issue trackers or restart the setup.

Desktop process changes must follow [desktop process ownership](contracts/desktop-processes.md), including descendant cleanup and console-free startup checks.

## Directory guide

| File | Responsibility |
|---|---|
| [.gitignore](.gitignore) | Paths excluded from version control. |
| [common.py](common.py) | Shared, domain-free helpers for all prototype modules. |
| [GLOSSARY.md](GLOSSARY.md) | Horizon（地平线） |
| [Horizon.cmd](Horizon.cmd) | Windows source-checkout launch command. |
| [LICENSE](LICENSE) | Project license. |
| [README.md](README.md) | Horizon（地平线） |
| [README.zh-CN.md](README.zh-CN.md) | Horizon（地平线） |
| [requirements-release.txt](requirements-release.txt) | Pinned distribution dependencies. |
| [requirements.txt](requirements.txt) | Python development/runtime dependencies. |

## Subdirectories

- [.github](.github/AGENTS.md) — GitHub Actions configuration.
- [agent](agent/AGENTS.md) — Embedded Pi runtime and Node build configuration.
- [archive](archive/AGENTS.md) — Read and migrate the local research archive.
- [contracts](contracts/AGENTS.md) — Versioned behavior and data contracts for public boundaries.
- [deploy](deploy/AGENTS.md) — Systemd service and timer templates for historical Linux deployment.
- [desktop](desktop/AGENTS.md) — Electron application and renderer build configuration.
- [docs](docs/AGENTS.md) — Project decisions, development conventions and acceptance evidence.
- [drafts](drafts/AGENTS.md) — Tracked initial knowledge-building examples.
- [editor](editor/AGENTS.md) — Editorial selection and review of research items.
- [fixtures](fixtures/AGENTS.md) — Offline research records and reproducible input examples.
- [horizon](horizon/AGENTS.md) — Research orchestration CLI and shared domain model.
- [hostrun](hostrun/AGENTS.md) — Desktop bridge, Agent tasks, Voyager retrieval, local knowledge and Keeper decisions.
- [initial_build](initial_build/AGENTS.md) — Initial local knowledge construction and review.
- [installer](installer/AGENTS.md) — Windows installation, cleanup and native runtime inputs.
- [knowledge](knowledge/AGENTS.md) — Knowledge CLI and Feishu Bitable adapter.
- [library](library/AGENTS.md) — Research PDF library management, reconciliation and search.
- [pdf_ingest](pdf_ingest/AGENTS.md) — PDF ingestion and page-level evidence anchors.
- [publisher](publisher/AGENTS.md) — Publication, alerts and interactive delivery cards.
- [question_queue](question_queue/AGENTS.md) — Persistent research question queue and CLI.
- [releases](releases/AGENTS.md) — Distribution channel index.
- [retriever](retriever/AGENTS.md) — Discovery adapters and retrieval CLI.
- [scripts](scripts/AGENTS.md) — Developer builds, compatibility probes and release tooling.
- [skills](skills/AGENTS.md) — Host-agent runtime skills.
- [tests](tests/AGENTS.md) — Behavioral, compatibility and integration checks.
- [trigger](trigger/AGENTS.md) — Keyword matching and trigger evaluation.
- [writer](writer/AGENTS.md) — Research writing, evidence review and rendering.

## Verification

Follow [hostrun](hostrun/AGENTS.md) to the Voyager service and its public tests, or [desktop](desktop/AGENTS.md) to the Electron build and window entry.

Generated output trees are outside this directory guide. The tracked historical push and figures in `outbox/` are examples of writer output. The same exclusion applies to local temporary runs, virtual environments, downloaded dependencies, build output and Git metadata.
