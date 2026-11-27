# Newsroom reports and historical Editor reads

## Newsroom and Archive

Newsroom starts writing after retrieval ends. Draft HTML is private to the current task cache. Report publication uses the Librarian and requires Explorer confirmation in that conversation. The Archive stores the immutable entry and HTML together. Interrupted writes are recovered before exposure. Reports use `r-` plus 32 lowercase hexadecimal digits. The entry records its title, summary, original SHA-256 and file location, and a nonempty ordered list of document IDs the report actually covers.

Import can preserve the identity, SHA-256 and correction provenance of a report whose HTML is unavailable. An available original must match its SHA-256. Imported coverage retains position and count. A missing document entry is represented by `d-unknown`. A known document whose file is offline retains its own ID. Neither later document registration nor credibility reassessment changes the immutable coverage list.

Credibility records carry the report ID, decision, reason and supporting evidence. The current decision is displayed separately from the immutable original. Changing report content requires a separate correction task and a new ID. Only confirmed publication of the replacement retires the old original and entry. The new record identifies its predecessor. A retired ID resolves to a deletion notice and is never redirected or reused.

When a scheduled task receives supplemental full text, the existing plan queue binds the source to a follow-up turn. Embedded Pi reads its bounded text through `archive_source late_fulltext` and can request registration of the original PDF. Newsroom then follows the same confirmed Archive publication flow. These actions preserve the plan's coverage and delivery history.

## Historical Editor records

The authenticated desktop bridge retains `report_open` and `report_citation` for saved Editor reports in their original conversation. Version-specific reads and zero-based citation indexes address the pinned evidence. A character offset opens a bounded page of the saved text. Abstract citation reads recheck the task grant, and permanently removed evidence is surfaced.

Existing Editor versions, original claims, saved writing preferences and Keeper evidence bindings remain readable. `report_feedback` edits or withdraws a rating on an existing saved version, with an operation ID and expected feedback revision. The feedback handoff keeps the pinned report and optional paragraph identity and opens an editable draft at the selected owner. These actions leave the saved report content intact.
