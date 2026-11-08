# Issue tracker: GitHub Issues

Repository: **Moke110/horizon**, remote `origin`. All issues, specs, tickets and wayfinder maps live in GitHub Issues. Use authenticated `gh`; inspect `gh auth status` and the actual command result before claiming a write succeeded.

**PRs as a request surface: no.**

## Conventions

- Spec: an issue labeled `spec` and `ready-for-agent` after the skill's approval gates. Its body is canonical; do not keep local drafts or mirrors.
- Ticket: one issue per approved vertical slice, normally `ready-for-agent`; set its source spec as the native parent. Do not close or rewrite the parent as part of decomposition.
- Wayfinder map: `wayfinder:map`; its native child tickets carry `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling` or `wayfinder:task`. Keep Destination / Notes / Decisions so far / Not yet specified / Out of scope in the map.
- Triage vocabulary: [triage-labels.md](triage-labels.md). Inspect labels before creating missing ones; preserve existing labels.
- Use native blocking edges. Ready horizon = open, unassigned children whose blockers are all closed. Claim a ticket by assigning yourself before implementation.
- Comments hold progress and resolution history; never append a Comments section or a Status field to the body. Open/closed is GitHub state.
- Refer to issues by their exact title linked to the issue URL. For an ambiguous issue/PR reference, read its type before acting.
- Temporary body files belong in the OS temporary directory; they are transport for `--body-file`, not local tracker entries. Clean up after a verified write.

## Commands

Replace placeholders with verified issue numbers and temporary paths. In PowerShell use actual newlines or argument arrays rather than Bash continuations.

```text
gh issue list --repo Moke110/horizon --state open
gh issue view <n> --repo Moke110/horizon --json title,body,comments,state
gh issue create --repo Moke110/horizon --title <title> --label <label> --body-file <temp-file>
gh issue edit <child> --repo Moke110/horizon --parent <parent>
gh issue edit <ticket> --repo Moke110/horizon --add-blocked-by <blocker>
gh issue edit <ticket> --repo Moke110/horizon --add-assignee @me
gh issue comment <ticket> --repo Moke110/horizon --body-file <temp-file>
gh issue close <ticket> --repo Moke110/horizon
gh api repos/Moke110/horizon/issues/<map>/sub_issues --paginate
gh api repos/Moke110/horizon/issues/<ticket>/dependencies/blocked_by --paginate
```

If a CLI flag is unavailable, inspect `gh issue edit --help` and use the native GitHub API. After publication, read back bodies, labels, parent and blocker relationships. Close work only with evidence for its acceptance criteria; consolidation closes replaced work with an explicit replacement link and reason.

## Document boundaries

- `contracts/` holds implementation schemas and state machines, not issue/spec copies.
- `docs/adr/` holds decisions; `docs/acceptance/README.md` summarizes evidence and remaining gaps. Long execution logs belong in issue comments or linked artifacts.
- `.scratch/` was the retired local tracker. Its history is in Git; do not recreate it. Old research, prototype and migration logs are also available through Git history. Samples and generated indexes belong beside fixtures, not in a second tracker.
