# Agent application service

The Agent owns tasks and their messages in `agent_tasks`, operation receipts in
`agent_operations`, and events in `agent_events`. Consumers call `AgentService`
commands and queries; they do not update these tables directly. These tables use
one SQLite database at the stable product data home, independent of installation.
The default is `%LOCALAPPDATA%/Horizon/data`; `HORIZON_DATA_HOME` must be absolute.
Initialization creates an empty store and does not import historical workspaces.

## Commands and queries

Commands use `schema_version: 1` and a nonempty `operation_id`.
`create_task` takes a nonempty `title` and returns a generated `task_id` (also
`entity_id`), `version: 1`, title and messages. Creation has no existing entity
version to compare. `append_message` takes `task_id`, integer `expected_version`,
role (`You` or `Horizon`) and text; it increments the task version exactly once.
Session and snapshot identifiers are reserved for slices that introduce those
entities; this slice does not invent session or snapshot identities.

Every public operation requires the current launch token. Queries `list_tasks`
and `events` expose committed state only. Task responses carry schema version,
operation ID, entity ID and current version. Events identify their operation and
task. A command result, operation receipt and event commit in one owner transaction.
An identical replay returns its original committed result before checking the
current entity version. Reusing an operation ID with different command content
fails. A failed command does not leave an event or partial task.

## Errors and concurrency

Authentication fails with `PermissionError`. Invalid commands raise `CommandError`
(a `ValueError`) with stable `code`: `unsupported_schema`, `invalid_command`,
`operation_conflict`, `not_found`, or `version_conflict`. Diagnostic messages are
not a machine protocol. A stale or missing message `expected_version` fails with
`version_conflict`; callers reload before issuing a new operation. Retries retain
all original command fields, including the original expected version.

The bridge authenticates before accessing the service. Model file tools must not
write the business database, snapshots or credentials. Model session files are
not the authoritative business ledger. Package/real GUI acceptance remains
tracked in GitHub and `docs/acceptance/`, separately from this protocol.

Generic model file writes are limited to `home/outbox/` artifacts. All other
home paths and archive paths are read-only through that tool, including future
business snapshots. Authenticated external send commands must supply their own
operation ID and expected version. Rejected commands refresh state and preserve
the submitted text for correction and retry.
