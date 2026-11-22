# Knowledge Frontier and Knowledge Card service

`hostrun.frontier.FrontierService` owns local Frontiers (关注问题) in the stable data home's `horizon.sqlite3`, beside Voyager and AgentService. A Frontier is the Explorer's focus question with an explicit research scope, hard constraints and a following/paused/resolved/deleted status. K1 stores no Frontier relations: an empty `relations` list is valid and expected. The service is the public application seam the desktop drives; it is not a retrieval scheduler and it does not import Keeper approval.

K2 adds manually edited Knowledge Cards with versioned source citations and related card IDs in the same data home.

## Local knowledge workbench search

The authenticated read-only desktop action `knowledge_search` searches persisted records owned by the Card, Theme and Frontier services in one database snapshot. The response event has the same name and echoes the supplied `operation_id`. It includes `results`, `total`, `offset` and `limit`. Each result has `kind`, `id`, `title`, `summary`, `status` and `version`. Selecting a result reads the complete committed record from its owner before it can be edited. Saves and citation reads retain each owner's version and operation checks.

The optional `kind` narrows the search to `card`, `theme` or `frontier`. `scope=active`, the default, returns active known Cards and Themes and following Frontiers. `scope=inactive` returns archived Cards and Themes and paused, resolved or deleted Frontiers. The workbench reads pending Keeper proposals through their separate query. A known Card marked questionable or awaiting a source remains active. An optional `related_to` containing `kind` and `id` filters to direct relations in either direction. This includes Card-to-Card and Theme-to-Card relationships. There are no Frontier relations at this stage.

Search is local and lexical. Unicode compatibility normalization and case folding allow different forms of the same letters and digits to match. One-character, two-character and longer Chinese text matches as a substring, including when it is mixed with English. An empty or whitespace-only query lists the chosen scope. Results are paged using a zero-based `offset` and a bounded `limit`.

## Keeper review requests

Keeper reviews bounded requests from a trusted Editor principal. In the pre-Editor stage, the explicit test adapter supplies this principal with test provenance. It pins an existing completed Agent turn as the test report and verifies its task and version. Each proposed change identifies the object, operation, expected version where applicable, reason, and saved source versions with locators and excerpts. The service derives the before and after values from committed knowledge and the requested edits.

The Agent dispatches a bounded, read-only Keeper child attached to the original task. Keeper checks the supplied evidence, duplicates and relationships. Independent changes appear on separate cards. Insufficient supporting evidence can return the request for more evidence. Pending confirmation data is persisted separately from committed knowledge. The child releases its execution slot when review finishes, and the completed report and newer plans can proceed while a proposal awaits a decision.

The authenticated `keeper_list` and `keeper_open` desktop queries serve pending and decided cards. Each card exposes grouped before and after values, reasons, source references, and its linked report and version.

The `keeper_test_request` desktop action is enabled only by the trusted launcher setting `HORIZON_KEEPER_TEST_REQUESTS=1`. Its `request` object includes a schema version, operation ID, task ID and version, report ID and version, and groups of changes. A change provides `kind`, `action`, `fields`, `reason` and saved `sources`. Updates and deletions also provide `id` and `expected_version`. Sources carry `material_id`, `material_version`, `locator` and `excerpt`. The adapter supplies the test principal itself. The initial response identifies the stored request, and `keeper_review` reports dispatch results and proposal IDs. Keeper's deterministic child checks the bounded inputs passed by the owners. A pending card records that the cited snapshot and anchor were verified and that the requested fields passed the owner's checks. Explorers review whether those sources substantiate the requested change.

In the test-enabled bridge, startup resumes queued requests with their existing identities. A stale pin results in a durable returned receipt, and a failed child results in a durable failed receipt. Either retains a recorded child identity when one exists. Replaying an operation can reread its outcome without launching a second completed review. Grouping follows direct and transitive connections between the requested changes across the request.

### Group decisions

The authenticated `keeper_decide` action binds one Explorer decision to a `proposal_id`, `revision`, ordered `expected_versions`, and `operation_id`. `expected_versions` follows the order of the changes on the displayed card, with `null` for a creation. `decision` is `approve` or `reject`, with an optional `reason`. The desktop adapter provides the trusted Explorer identity. Keeper and Editor cannot decide.

Approval validates all expected versions and material references before committing any change. Knowledge records, material bindings, owner operation receipts and events, and the proposal status, receipt and audit commit together. A validation failure leaves the group unapplied. Rejection records the decision without writing knowledge. The same operation and payload replay the original receipt. An operation ID with different payload conflicts, and an already-decided revision cannot be applied again. `keeper_open` returns the saved decision on a terminal card, and authenticated `keeper_audit` reads its audit record.

### Revision and Explorer feedback

If an approval matches the displayed proposal and its ordered pins but a target has since changed, the transaction records the conflict and queues one durable Keeper review. The receipt reports `status=revising`, `code=version_conflict`, an empty `applied` list, and the `review_id`. Commands against a different proposal revision or a different set of displayed pins fail before scheduling a review. A review starts a read-only Keeper child on the same original task and keeps the original completed report ID and version. New conversation turns can continue independently.

The authenticated `keeper_revise` command takes `schema_version: 1`, `operation_id`, `proposal_id`, `revision`, and `decision: adjust|cancel`. An adjustment requires Explorer feedback of at most 500 UTF-8 bytes. It can supply `omit_changes` as displayed change indexes (starting at zero) or `keep_current_fields` as entries with a `change_index` and a list of proposed field names. The feedback is persisted with the original task's review and appears in the audit and child input. The choices govern which modifications Keeper reviews. With feedback alone, Keeper rechecks the existing changes.

Keeper rebuilds the selected changes against current owners, preserves unrelated manual edits, and rechecks source and relationship pins. If a target moves again during review, the service verifies the plan again with a bounded retry. If a safe plan is unavailable it records a returned card. A successful review publishes the same proposal ID at the next revision. `keeper_open` serves the current status and `revision_notes` with the previous reviewed changes, feedback and the new review result. Only an explicit new approval with that revision and its displayed versions can apply the group. Cancellation prevents later review publication. The review queue, feedback, audit and decisions remain available across a restart.

Reissuing the same rejected change on the same grounds returns no new card. An explicit `supersedes_proposal_id` and `new_evidence_reason` describe why the same change should be considered with new evidence. A recaptured or reordered copy of the same evidence does not satisfy that requirement.

## Version history and undo

The authenticated desktop actions `knowledge_history`, `knowledge_undo_preview` and `knowledge_undo` emit events of the same name. Each takes `schema_version: 1`, `kind`, `id` and `operation_id`. The last two accept `target_version`, which identifies the version produced by the edit being undone. The history response carries `current_version`, `total`, `offset`, `limit` and newest-first `entries`. Each entry shows its committed version, action, Explorer, time, optional reason, and the complete snapshots before and after. The first version has no previous snapshot. Manual mutation commands accept an optional `reason` of up to 500 UTF-8 bytes. Older edits without a recorded reason remain readable.

History reads the same immutable operation receipts as before an upgrade. Undo preview shows the selected edit's original field values, current values, proposed values, and any overlap with later changes. A later change to the same field blocks undo even if it subsequently returned to the value set by the selected edit. Fields already at their original value are skipped. Frontier scope and constraint fields are compared independently, and the combined result is checked by the owner. Evidence and relation lists are compared as units. Theme relationships restored by undo must still pass the owner's relation checks.

`knowledge_undo` requires the previewed `expected_version` and `preview_token` issued by the current desktop bridge process. Under one write transaction the service rechecks the current version, current source availability, proposed changes and owner validation. A stale or conflicting preview yields an error and makes no change. Undoing an edit or an earlier undo creates a new version only when values change. It leaves historical versions intact, preserves unrelated later edits, and commits the new state, operation receipt and event together. Undo returns the committed record and an identical retry returns that same receipt. Historical and current reads project the removed status of saved source references, including for an old receipt whose source was later deleted. Archived Cards and Themes and deleted Frontiers keep readable history. Creation, archival and deletion cannot be undone through this action.

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

## Themes

K3 adds locally maintained Themes (命题): an Explorer's evidence-backed judgement across sources. A committed Theme carries `schema_version: 1`, the `operation_id` that produced it, `theme_id`, integer `version`, `judgement`, `conditions`, `related_cards`, `evidence`, derived `evidence_sufficiency`, `known`, `origin: "manual"`, `status`, `created_at` and `updated_at`.

- `judgement` is nonempty and at most 4000 UTF-8 bytes. `conditions` holds at most 16 bounded strings of up to 2000 bytes each.
- `related_cards` holds at most 32 entries shaped `{card_id, version}`. A caller may supply a card ID alone or a pinned version.
- `evidence` holds at most 64 entries shaped `{evidence_id, stance, reason, source}`. `stance` is `supports` or `opposes`; `reason` is nonempty and at most 4000 bytes. The service assigns `evidence_id` on creation and preserves it on update. Source input is `{material_id, locator, excerpt, material_version?}`. The desktop pins the saved material version. The material service verifies that the excerpt belongs to the selected snapshot page or paragraph and records the material version, digest, snapshot identity and exact character range. A locator such as `第 2 页` or `p6` disambiguates repeated text. Editing a reason or direction alone preserves its pinned source.
- `evidence_sufficiency` is derived from recorded stances, never from caller input: `unsupported` with no evidence, `single_sided` with only one stance, and `balanced` when support and opposition coexist. There is no minimum number of related cards or evidence entries; a single-sided Theme is saved and stays readable as insufficient.
- Support and opposition coexist. Adding evidence never rewrites `judgement`; only an explicit update with the expected version changes it. A newer source does not automatically replace an older judgement or an earlier pinned citation.

### Theme commands and citations

- `theme_create` commits version 1. `theme_update` changes supplied editable fields. `theme_archive` makes a Theme read-only and increments its version. Mutations require schema version 1 and an operation ID; update and archive also require the Theme ID and integer expected version. `status` is not an edit field.
- `theme_open` reads the full committed Theme and echoes a supplied read operation as `read_operation_id`. `theme_list` and desktop state `themes` contain active and archived summaries. `theme_query` calls authenticated `KnowledgeThemeService.query` for the active known set by default, with a bounded `theme_ids` selection or explicit `include_archived`.
- `theme_source` calls `KnowledgeThemeService.open_evidence` for one evidence entry. The desktop sends `theme_id`, `evidence_id`, `expected_version` and a matching `operation_id` for the read. The service checks the displayed Theme version and re-reads the pinned snapshot binding and returns `status` (`available` or `removed`), material and snapshot identity, the pinned title and source, the Explorer's locator and excerpt, and for available snapshots an `anchor` plus the actual `segment`. The anchor identifies the pinned PDF page or saved paragraph. Long segments show a bounded excerpt context. Evidence uses `source` for citations, with `Report` reserved for the explanation.
- Opening a citation never depends on the material still being live. After permanent removal the view reports `removed` and keeps the historical citation. Theme citation owners appear in material deletion impact as `theme:<theme_id>:<content_hash>`.

### Relations, errors and concurrency

Related cards are resolved through `KnowledgeCardService.read_in_transaction` on the same transaction as the Theme write. An unknown card fails with `not_found`; an archived card fails with `invalid_relation`; a stale pinned version fails with `version_conflict` whose details have `kind: "related_card"`, the card ID, expected/current versions and the current card. A Theme mutation, its receipt, new citation bindings and its event commit together, so a failed relation or evidence check leaves no Theme, no relation and no material binding behind.

A stale Theme edit returns `version_conflict` with `kind: "theme"`, expected and current versions, current Theme, submitted fields and differing field names. An archived Theme rejects edits with `invalid_transition`. Other stable errors include `invalid_theme`, `invalid_query`, `invalid_relation`, `invalid_status` and `not_found`. An identical replay returns the original committed result before version checks; reusing an operation ID with different content fails with `operation_conflict`.

The desktop bridge exposes `theme_create`, `theme_update`, `theme_archive`, `theme_open`, `theme_list`, `theme_query` and `theme_source`. It emits `theme_view` after a mutation or open, `theme_list` for listings, `theme_query` for reads and `theme_source` for citation opening. State carries `themes`, and a committed mutation emits `theme_changed` so the desktop can refresh.

## Voyager scope and snapshots

Desktop plan creation uses the descriptor `scope: {frontier_id, expected_version?}`. The knowledge provider resolves the committed Frontier into a persisted snapshot with `kind: "frontier"`, `frontier_id`, `frontier_version`, `text`, `alternatives`, `must_include`, `exclude` and `sources`. A bare scope object remains the backward-compatible `kind: "temporary_explicit"` path for the manual 范围检索 panel.

- Only a `following` Frontier accepts a new retrieval plan or a subsequent provider execution. Pausing, resolving or deleting stops future retrieval with `frontier_inactive`; already produced requests, candidates and materials stay readable and are never rewritten.
- A Voyager request records its own scope snapshot. Later Frontier edits do not change an existing request's text or `frontier_version`.
- Creating a plan and running source retrieval are separate actions. Creation records the request and queries but does not contact a provider; `voyager_execute` performs actual source access under the request budgets.
- Retrieval does not depend on model configuration.
- When a desktop task is available, `voyager_create` may include its `task_id`. Task binding validates that the task exists and never accepts an unauthenticated or other task's Frontier for a Voyager child. The legacy explicit temporary scope remains available alongside the same-task committed Frontier scope.

## Acceptance boundary

This contract defines service and desktop behavior. Automated checks do not establish live source or cloud Base acceptance; that evidence belongs in `docs/acceptance/` and GitHub Issues.
