# Knowledge Frontier and Knowledge Card service

`hostrun.frontier.FrontierService` owns local Frontiers (关注问题) in the stable data home's `horizon.sqlite3`, beside Voyager and AgentService. A Frontier is the Explorer's focus question with an explicit research scope, hard constraints and a following/paused/resolved/deleted status. K1 stores no Frontier relations: an empty `relations` list is valid and expected. The service is the public application seam the desktop drives; it is not a retrieval scheduler and it does not import Keeper approval.

K2 adds manually edited Knowledge Cards with versioned source citations and related card IDs in the same data home.

## Entity and validation

A committed Frontier carries `schema_version: 1`, the `operation_id` that produced the current committed version, `frontier_id`, integer `version`, `question`, `status`, `scope`, `hard_constraints`, `relations`, `created_at` and `updated_at`.

- `question` is nonempty and at most 1000 UTF-8 bytes.
- `scope.text` is nonempty and at most 4096 bytes. `scope.alternatives` holds at most 16 bounded strings; `scope.sources` is a nonempty bounded list of source identifiers matching `[A-Za-z0-9_.-]+`.
- `hard_constraints.must_include` and `exclude` are bounded lists of terms.
- `status` is `following`, `paused`, `resolved` or `deleted`. Creation defaults to `following`. `deleted` is terminal and is reached through `delete`, not through an edit.
- `relations` must be a list; K1 accepts the empty list. Later slices may add typed relations, but no Keeper workflow is introduced here.

## Commands, replay and concurrency

Every mutation requires `schema_version: 1` and a nonempty `operation_id`; after creation it also requires `frontier_id` and integer `expected_version`.

- `frontier_create` takes question, status, scope, hard constraints and relations and commits version 1.
- `frontier_open` reads the full committed Frontier. The list projection truncates `scope_text` for display; it is never an edit source.
- `frontier_list` returns bounded summaries: id, version, question, status, truncated scope text and update time.
- `frontier_update` changes supplied fields, increments `version` exactly once and rejects stale `expected_version` with `version_conflict`. A deleted Frontier rejects edits with `invalid_transition`.
- `frontier_delete` marks the Frontier `deleted`, increments the version and keeps the row, earlier retrieval records and materials intact.

An identical operation replay returns its original committed result before version checks. Reusing an operation ID with different content fails with `operation_conflict`. Other stable errors include `invalid_scope`, `invalid_status` and `not_found`. Mutations commit the Frontier, its operation receipt and its event together.

The desktop bridge exposes `frontier_create`, `frontier_open`, `frontier_list`, `frontier_update` and `frontier_delete`. It emits `frontier_view` with the full committed view after a mutation or open, and `frontier_list` for listings.

For a Frontier command error, the bridge's `command_error` carries the failed `operation_id` together with its `code` and `message`. It includes no committed view; an error is never a successful save and must not clear an edit draft.

## Frontier desktop editing behavior

- Opening an edit requests and uses the full committed view only. List summaries are never written into the form.
- The edit is pinned to the version it loaded. Background refreshes do not rebase the draft; saving after a newer commit fails with `version_conflict`, and the draft remains for review.
- A draft is cleared only after a successful `frontier_view` whose `operation_id` matches the save operation. Errors keep the draft.
- Status can be chosen while creating or editing among following, paused and resolved. Explicit pause, resolve, re-follow and delete controls remain available from the list.
- Deleted Frontiers show no edit action. Deleting while an edit is open exits the edit and keeps the history message.
- Saves commit directly. Keeper approval, relation editing and scheduling are out of scope for K1.

## Knowledge Cards

Cards have a stable ID, committed version, operation ID, manual origin and timestamps. Each requires title and content. Aliases and recall terms each allow 32 entries of up to 256 UTF-8 bytes. Relations contain at most 32 existing card IDs. Self-relations are rejected.

Source input has the shape `{material_id, locator, excerpt}`. The service verifies the excerpt against the saved material text and pins the version, hash, title, source kind and snapshot ID when available. Whitespace is normalized for validation. The locator is supplied by the Explorer. Unchanged citations preserve their pinned versions. Citation owners appear in material deletion impact. After permanent removal, card reads mark that source `removed` and retain the historical citation and excerpt.

At least one verified citation from captured public web or academic material yields `provenance: sourced`. Local user input or an empty citation list yields `user_statement` and `source_pending: true`. The independent `questionable` flag records doubt. Archival excludes a card from the active known set and leaves its history readable. Reading, rating and retrieval leave known state unchanged.

### Commands and queries

- `card_create` commits version 1. `card_update` changes supplied editable fields. `card_archive` makes a card read-only and increments its version. Mutations require schema version 1 and an operation ID. Update and archive also require the card ID and integer expected version.
- `card_open` reads the full committed card. It echoes a supplied read operation as `read_operation_id`. `card_list` and desktop state `cards` contain active and archived summaries.
- `card_query` calls authenticated `KnowledgeCardService.query`, the public read boundary for Voyager and the Editor. It returns active known cards with committed versions by default. Callers may pass a bounded `card_ids` selection or opt in to archived records with `include_archived`.

A card mutation, its receipt, new material bindings and its event commit together. An identical replay returns the original committed result. Reusing its operation ID for a different command fails.

A stale edit returns `version_conflict` with the operation ID, expected and current versions, current card, submitted fields and differing field names. The desktop retains the draft and shows differences. The Explorer can explicitly adopt the latest version as a new base or discard the draft.

## Voyager scope and snapshots

Desktop plan creation uses the descriptor `scope: {frontier_id, expected_version?}`. The knowledge provider resolves the committed Frontier into a persisted snapshot with `kind: "frontier"`, `frontier_id`, `frontier_version`, `text`, `alternatives`, `must_include`, `exclude` and `sources`. A bare scope object remains the backward-compatible `kind: "temporary_explicit"` path for the manual 范围检索 panel.

- Only a `following` Frontier accepts a new retrieval plan or a subsequent provider execution. Pausing, resolving or deleting stops future retrieval with `frontier_inactive`; already produced requests, candidates and materials stay readable and are never rewritten.
- A Voyager request records its own scope snapshot. Later Frontier edits do not change an existing request's text or `frontier_version`.
- Creating a plan and running source retrieval are separate actions. Creation records the request and queries but does not contact a provider; `voyager_execute` performs actual source access under the request budgets.
- Retrieval does not depend on model configuration.
- When a desktop task is available, `voyager_create` may include its `task_id`. Task binding validates that the task exists and never accepts an unauthenticated or other task's Frontier for a Voyager child. The legacy explicit temporary scope remains available alongside the same-task committed Frontier scope.

## Acceptance boundary

This contract defines service and desktop behavior. Automated checks do not establish live source or cloud Base acceptance; that evidence belongs in `docs/acceptance/` and GitHub Issues.
