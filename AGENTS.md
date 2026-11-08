# horizon

This file contains development-agent working instructions only. Keep product positioning and feature explanations out of this file. Read `docs/adr/` for confirmed decisions and `GLOSSARY.md` for terminology.

## Development responsibilities

- **Planning:** Codex handles complex work (architecture, cross-module changes, unclear requirements, or risky migrations); DeepSeek Harness handles simple, bounded work. Use the Matt Pocock workflow: `wayfinder` to map uncertain work, `grill-with-docs` to resolve decisions and record domain docs, `to-spec` to publish the agreed spec, then `to-tickets` to publish approved vertical slices and native blocking links. The skill is named `to-tickets` (plural).
- **Implementation:** DeepSeek Harness uses `implement` to execute the approved GitHub spec and tickets. Read the issue body, comments and blockers before starting; work only on unblocked tickets, and follow the skill's testing, review and commit requirements. Record results and remaining blockers in GitHub; close a ticket only when its acceptance criteria are met. Offline tests do not replace required live acceptance.
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

## Release, tags and channels

Distribution releases are built from tags; branches are not release units. Users install the distribution package; the source checkout stays the developer path (see `skills/horizon-run/references/installation.md`).

- **Tag convention:** `vMAJOR.MINOR.PATCH` (for example `v1.2.3`) is the **stable** channel. `vMAJOR.MINOR.PATCH-<suffix>` (for example `-rc.N`, `v0.1.0-rc.1`, or `v0.2.0-preview.2`) is the **prerelease** channel. The `v` prefix is not part of the version or the artifact name: `v0.1.0-rc.1` publishes `horizon-0.1.0-rc.1-prerelease.zip`.
- **Tag-triggered release:** pushing a `v*` tag runs `.github/workflows/release.yml` (windows-latest, `contents: write`). It derives version/channel from the tag, downloads the pinned embeddable CPython and wheels, assembles `runtime/`, builds the package twice to prove the artifact digest is reproducible, creates the GitHub Release with the zip, `.digest.json` and `manifest.json` (`--prerelease` only for prerelease tags), and updates `releases/index.json` with version, channel, artifact digest and compatibility. The workflow commits that index to `main` as `github-actions[bot]` and uploads it as a release asset; if branch protection rejects the push it retries once and then fails, leaving the index to be updated manually.
- **Dry runs:** `workflow_dispatch` builds and verifies but creates no Release and commits nothing unless `dry_run` is explicitly false.
- **Main first, then tag:** `main` must already contain the commit that will be tagged; the index job verifies the tagged commit is an ancestor of `origin/main` and refuses to commit the index otherwise. Merge the change into `main`, verify the suite there, then tag that commit.
- **Do not push tags for test runs:** a tag creates a public GitHub Release and writes the release index. Rehearse with a manual `workflow_dispatch` dry run instead. Never push a tag without the maintainer's explicit intent to publish.

The observable release contract is [`contracts/release.md`](contracts/release.md); the package wrapper/runtime layout is [`contracts/distribution-runtime.md`](contracts/distribution-runtime.md).

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

### Skill pack

Read the relevant `SKILL.md` before applying a skill. Shared Matt Pocock skills are installed under `~/.agents/skills/<name>/`; source metadata lives in `~/.agents/.skill-lock.json`. Codex and DeepSeek Harness should use their supported skill-loading mechanism against this shared source, without relying on WorkBuddy junctions. If a required skill is unavailable, report the missing integration rather than inventing its behavior.

The planning/implementation skills above are user-invoked workflows. Follow each skill's approval gates and delegated skills (including testing and code review); do not automatically start implementation merely because its skill is installed.

The per-repo configuration those skills read is `docs/agents/issue-tracker.md`, `docs/agents/triage-labels.md` and `docs/agents/domain.md`. Edit those files directly; `setup-matt-pocock-skills` only needs re-running to switch issue trackers or restart the setup.

Desktop process changes must follow [desktop process ownership](contracts/desktop-processes.md), including descendant cleanup and console-free startup checks.
