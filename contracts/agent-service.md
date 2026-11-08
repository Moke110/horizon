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
`entity_id`), a stable `session_id`, `version: 1`, title and messages. Creation has no existing entity
version to compare. `append_message` takes `task_id`, integer `expected_version`,
role (`You` or `Horizon`) and text; it increments the task version exactly once.
Each task has one independent Pi session identity, preserved across turns and application restarts. Existing tasks receive a session identity without changing their messages or version. Snapshots remain reserved for later slices.

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

Pi conversation JSONL is stored privately below the data home agent directory. The bridge sends only the new user message and the task session identity. JSONL restores model context; it never establishes a committed business operation. Model configuration belongs to the same stable data home and is not imported from legacy workspaces.

For an existing task without a Pi transcript, the desktop supplies committed prior messages once to initialize model context. Subsequent turns restore the Pi transcript without replaying that bootstrap. The temporary bootstrap file stays under the private agent directory and is removed when the worker exits.

### Model discovery

The authenticated desktop bridge queries provider model catalogs in the backend. Renderer state contains provider choices, model identifiers and the selected model, never credentials. Configuration is replaced only after successful discovery. Selection must belong to the saved catalog. Refresh uses the saved credential without returning it. Catalog visibility is not proof of inference permission or available quota.

### Child sessions

`start_child` accepts schema_version, operation_id, parent task_id, input_version, bounded input_text and a whitelisted development_reader/development_summarizer role. `finish_child` requires the child expected_version and commits status succeeded/failed, result and error under a new operation_id. Child identity, parent session, role, input version and stable agent-child:<child_id> artifact reference remain in the Agent-owned SQLite ledger. `list_children(task_id)` returns only that task's committed children. Operation replay never replaces successful sibling results.

The primary Pi delegate tool sends a structured request to its trusted Python owner; the owner invokes these public commands and starts an independent Pi session. Children cannot delegate or access generic business CLI, write tools, credentials, databases or sibling transcripts. Initial roles expose development-only read tools; production module role contracts are introduced by their later tickets. Child JSONL is model context, not business completion evidence. Full cancellation/lifecycle acceptance remains in #133.

Delegation operation identities are scoped by parent task, committed input version and provider tool-call identity. Repeating a call in one turn returns its existing child result; reusing a provider call identifier in a later turn creates a separate child.

### Recoverable desktop turns

`begin_turn` commits the user message and running turn together. `commit_turn` checks expected_version and commits the completed response with the assistant message; failed, cancelled and recovery_required states do not append a success response. Operation receipts precede version checks on replay. `recoverable_turns` returns running work after process loss. Pi branches to the persistent turn marker on replay, but only SQLite commits establish completion.

Desktop recoverable turns expose read_file and development-role delegate only. Legacy mutating horizon_cli/write_file tools remain available to legacy host workflows, not this replay path. Child operations use parent input version plus semantic input and occurrence identity: repeated identical calls within one turn remain distinct; matching recovered occurrences reuse committed owner results. Unknown legacy effects are never automatically replayed.

`attempt_turn` records fresh context version query results. The Stage-B provider is explicitly development_empty, available=false, schema/version 1 for knowledge and preferences; it is not production knowledge or preferences. Each new child records its snapshots; previously committed child artifacts retain theirs on recovery. Execution, material waiting, proposals and delivery are distinct GUI categories; later module tickets supply the currently absent waiting items.
