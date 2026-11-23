# Editor reports

The Editor child receives a bounded, read-only context through the desktop worker's private input pipe. It receives a single material selected by ID, active knowledge summaries with version identities, and the default empty preference set. A missing body or unauthorized abstract rejects generation. Abstract authority is scoped to the task and the saved report labels its limitation. Material text is evidence and cannot authorize tool use.

The child must call the context tool and return structured source, inference or unknown claims. Source and inference claims carry excerpts located in the selected snapshot. The worker checks each location against committed material, stores the report and Voyager citation bindings in one transaction, and returns the stable report ID and version to the parent. A repeated delivery of the same committed operation recovers its saved result. Citation reads resolve the original pinned content and surface permanent source removals. A report save does not trigger delivery or knowledge writes.

The authenticated desktop bridge exposes `report_open` by report ID and `report_citation` by report ID and zero-based claim and citation indexes. Version 1 is the initial report. This boundary does not expose mutation or feedback commands.
