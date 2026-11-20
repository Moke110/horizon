# Voyager explicit scope service

`hostrun.voyager_service.VoyagerService` owns temporary explicit retrieval requests in the stable home database through managed database connections. It does not create Frontiers or infer verified retrieval coverage from model prose or scheduling execution declarations.

## Public boundary

All mutations require a launch token, schema_version 1, a unique operation_id and (after creation) request_id and expected_version. Replay is checked before version/budget checks; identical operations return their durable receipt without a second provider call or charge. Conflicting reuse is rejected. Receipt, budget change and event commit together. Provider IO never holds a database connection.

`create` preserves scope.text, user/planner alternatives, must_include, exclude and source identifiers. There are at most 128 expanded queries. Hard constraints remain on every actual query and accepted candidate. Initial searches separate foundation (effective lower bound zero) and recent queries; incremental searches use the original lower bound minus overlap. UTC timestamps are integer Unix seconds. Windows are half-open [effective_from_at, until_at). Original user bounds are retained separately. Publication and discovery timestamps are independent; missing selected timestamps do not prove inclusion.

`execute` calls configured source owners with an actual query DTO and remaining candidate/time allowance. The default registry includes `arxiv` and `europepmc`. An explicitly supplied registry replaces the defaults, including when empty. A missing source records an unconfigured gap. An exhausted validated page/receipt completes that query only. Success cursors are query-specific and never replace unresolved gaps.

## Budgets and recovery

Defaults per segment: seconds 900, queries 20, candidates 100, fulltexts 20. Any zero or exactly reached limit stops with partial coverage, including when the final page exhausts the source. Query and fulltext reservations charge before external work. Candidate use counts returned rows including rejected/duplicate rows. Time is accounted at reservation and result commit. A late result may retain evidence but cannot erase a time gap or advance its cursor.

Explicit Continue creates a new segment, retaining aggregate usage, results and gaps. A per-request nonblocking OS advisory lock rejects concurrent execution; process death releases ownership, not the durable reservation. Continuing an unknown request requires acknowledge_unknown=true, preserving its prior charge and unknown attempt. No automatic free replay follows a crash. Cancel rejects late commits and further continuation; committed earlier results remain.

`reserve_fulltext` precharges future source-owner work; `record_fulltext` stores a bounded owner evidence reference rather than body content. Pending/failed/unknown body work prevents complete status. Source/material owners supply their own evidence and schemas.

## Persistence and presentation

Requests, operation receipts and events are Voyager-owned SQLite tables, initialized by this service rather than importing Voyager into AgentService schema setup. Product startup initializes the service on the target home. Managed upgrades preserve the database; first public service access creates its additive tables.

`recent` pages 50 summaries; `view` bounds candidates to 50 (abstract omitted), gaps to 100, fulltext work to 50 and segment history to 3. `query_history` pages ten actual attempts. Candidate pages are bounded to 2 MB metadata; error pages permit only a fixed error_code. Raw provider errors, secret-bearing URLs and arbitrary response bodies are not persisted. Source IO occurs outside the desktop dispatch lock; postcommit notifications refresh authenticated GUI projections.
