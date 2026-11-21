# Voyager explicit scope service

`hostrun.voyager_service.VoyagerService` owns temporary explicit retrieval requests in the stable home database through managed database connections. It does not create Frontiers or infer verified retrieval coverage from model prose or scheduling execution declarations.

## Public boundary

All retrieval mutations require a launch token, schema_version 1, a unique operation_id and (after creation) request_id and expected_version. Replay is checked before version/budget checks; identical operations return their durable receipt without a second provider call or charge. Conflicting reuse is rejected. Receipt, budget change and event commit together. Provider IO never holds a database connection.

`create` preserves scope.text, user/planner alternatives, must_include, exclude and source identifiers. There are at most 128 expanded queries. Hard constraints remain on every actual query and accepted candidate. Initial searches separate foundation (effective lower bound zero) and recent queries; incremental searches use the original lower bound minus overlap. UTC timestamps are integer Unix seconds. Windows are half-open [effective_from_at, until_at). Original user bounds are retained separately. Publication and discovery timestamps are independent; missing selected timestamps do not prove inclusion.

`execute` calls configured source owners with an actual query DTO and remaining candidate/time allowance. The default registry includes `arxiv` and `europepmc`. It also includes public Crossref (`crossref`) and OpenAlex (`openalex`), which requires an API key from a verified OpenAlex account and a successful quota check before each search page. The owner retains optional DOI, up to 100 reference IDs, the provider’s reference count and cited-by count. Null counts indicate missing data. The query result keeps a separate row for each source identity. An explicitly supplied registry replaces the defaults, including when empty. A missing source records an unconfigured gap. An exhausted validated page/receipt completes that query only. Success cursors are query-specific and never replace unresolved gaps.

`source_settings` and `save_source_setting` require a launch token and return only whether each key is saved, supplied by the launch environment or missing. The desktop can set or remove the saved key for `openalex` or `tavily`. Saved keys override `OPENALEX_API_KEY` and `TAVILY_API_KEY` and take effect on the next provider attempt. A key being present does not establish authorization or quota. Credentials are stored in the local managed credential file outside the Voyager database, request receipts, diagnostics and model configuration pipe.

## Budgets and recovery

Defaults per segment: seconds 900, queries 20, candidates 100, fulltexts 20. Any zero or exactly reached limit stops with partial coverage, including when the final page exhausts the source. Query and fulltext reservations charge before external work. Candidate use counts returned rows including rejected/duplicate rows. Time is accounted at reservation and result commit. A late result may retain evidence but cannot erase a time gap or advance its cursor.

Explicit Continue creates a new segment, retaining aggregate usage, results and gaps. A per-request nonblocking OS advisory lock rejects concurrent execution; process death releases ownership, not the durable reservation. Continuing an unknown request requires acknowledge_unknown=true, preserving its prior charge and unknown attempt. No automatic free replay follows a crash. Cancel rejects late commits and further continuation; committed earlier results remain.

`reserve_fulltext` precharges future source-owner work; `record_fulltext` stores a bounded owner evidence reference rather than body content. Pending/failed/unknown body work prevents complete status. Source/material owners supply their own evidence and schemas.

## Persistence and presentation

Requests, operation receipts and events are Voyager-owned SQLite tables, initialized by this service rather than importing Voyager into AgentService schema setup. Product startup initializes the service on the target home. Managed upgrades preserve the database; first public service access creates its additive tables.

`recent` pages 50 summaries; `view` bounds candidates to 50 (abstract omitted), gaps to 100, fulltext work to 50 and segment history to 3. `query_history` pages ten actual attempts. Candidate pages are bounded to 2 MB metadata; error pages permit only a fixed error_code. Raw provider errors, secret-bearing URLs and arbitrary response bodies are not persisted. Source IO occurs outside the desktop dispatch lock; postcommit notifications refresh authenticated GUI projections.
## Tavily web discovery

Select source `tavily` and supply a user-owned key in **范围检索 → 检索来源 API** or through the launch environment. The key is used only in HTTPS authorization headers. Search snippets are discovery metadata, never full-text evidence. Only Search basic is enabled; automatic parameters, answers and raw content are disabled.

Before each search, `/usage` must positively identify the free Researcher plan, zero PAYG usage/limit and at least one available account/key credit. A durable local ceiling and reservation precede Search. Unknown, exhausted or interrupted usage pauses this channel; other sources remain available. The local ceiling does not reset automatically across months. An interrupted reservation requires reconciliation against verified account usage before resuming; do not erase the ledger to retry an unknown call.

Protocol references: [Usage](https://docs.tavily.com/documentation/api-reference/endpoint/usage), [Search](https://docs.tavily.com/documentation/api-reference/endpoint/search).

## Subscription discovery

Adding a feed or section is idempotent by kind and URL. Refresh records its result, validators, and each changed entry version in one SQLite transaction. Concurrent refreshes compare the subscription version before committing. A malformed feed or retrieval failure records a gap while retaining last success, identities and validators. RSS/Atom conditional requests use ETag and Last-Modified. A 304 creates zero versions. Section requests visit at most three pages, restricted to the configured HTTPS host and path; other anchors are never fetched. Each response is bounded to 2 MiB and each round to 500 entries. The desktop lists current configuration, latest round changes and last success. Voyager source `sub_<subscription ID>` reads stored version candidates ordered by receipt, retaining publication and discovery times separately.
