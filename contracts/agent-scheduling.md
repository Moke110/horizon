# Agent scheduling

Agent owns schedules, execution claims and coverage cursors in the product SQLite database. `AgentService.schedule_command` and `list_schedules` require the current launch token. Scope is `development_explicit`: bounded developer-stage execution text, not a Frontier or a claim that source retrieval occurred.

Commands carry `schema_version: 1` and an `operation_id`. Create requires `title`, `scope`, positive integer `interval_seconds`, `start_at` and `next_at`. Edit requires `schedule_id` and `expected_version`; it may change title, scope, interval or next trigger. Actions `pause` and `resume` affect future admission. Action `abandon_run` is allowed only for a coverage-pending run; it preserves the cursor, clears that claim and pauses its schedule. Commands persist their operation receipt and event with the changed schedule; replay returns the original result and conflicting reuse is rejected.

Scheduling timestamps and intervals use integer UTC Unix **seconds**, rather than display-local times. Trigger claims cover `[from_at, until_at)`. Repeated offline triggers merge into the latest due boundary. A schedule retains at most one active claim; a paused or failed task blocks that schedule while releasing its execution position. New triggers during an old run remain pending. Completion of execution does not wait for a later knowledge proposal decision.

The desktop prioritizes existing recoverable foreground work before admitting new due schedules. Persistent run binding creates one task, session and turn atomically. Repeating a binding cannot append another user input. Independent scheduled workers retain separate task/session ownership. Default execution limits are two main workers and four child workers, configurable from 1 through 32. Child waiters consume no child position and cancellation prevents them from entering execution. Desktop admission is serialized with interactive sends; scheduler claims are transactional. These desktop slots belong to one contained desktop owner, not a machine-wide distributed lease service.

## Development execution coverage

Successful model output alone does not advance a cursor. The bounded explicit workflow requests this final JSON object:

```json
{"schema_version":1,"result":"execution result","coverage":{"from_at":100,"until_at":160,"covered_until":140,"status":"partial"}}
```

The coverage object has exactly the four shown keys. Its three timestamps must be integers; its start/end must match the persisted claim, and `covered_until` must lie inside that interval. Status must be `complete` at the end, `uncovered` at the start, otherwise `partial`. This is a validated **development execution declaration**, not independently verified source-retrieval evidence. Voyager integration must supply its own actual coverage evidence; this contract does not fabricate it from prose.

A valid complete declaration advances to its end. A valid partial declaration advances only the declared prefix; the remainder is retained for a subsequent normal trigger. Missing, malformed or uncovered declarations leave the cursor unchanged and hold the claim as `coverage_pending`, preventing automatic replay. The GUI shows the held state; explicit abandonment pauses that schedule without authorizing skipped coverage. Completed results remain available in their task.
