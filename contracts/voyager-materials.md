# Offline material snapshots

`MaterialsService(home, token=...)` owns Voyager material records, operation
receipts and events in the stable product SQLite database. It does not discover
or import legacy workspaces. The desktop uses the current launch token.

`register_text` accepts schema_version 1, operation_id, title, source_label and
text. Fields are bounded; unknown fields are refused. Source kind is always
`user_input`; received_at is assigned by the service in UTC and is not a claimed
publication time. UTF-8 bytes are preserved exactly. Snapshot metadata includes
SHA-256, byte length, a snapshot ID and extraction version `pasted_text.v1`.
Each registration has distinct material/source/snapshot identities; matching
content can share one blob. Replaying an operation returns its original result;
conflicting reuse is rejected. No model or network is needed.

The complete blob is published atomically before its material, operation and
event transaction commits. Failure before publication or SQL commit leaves no
partial reference. Failure after publication may leave an unreferenced complete
blob; replay verifies and reuses it. Failure after SQL commit returns the same
result on replay. Existing content is digest-verified, never overwritten.

Ordinary homes store blobs under `snapshots/voyager-text/<sha256>.txt`. Managed
homes derive their bound archive via the managed import service and store blobs
under `originals/voyager-text/`; native identity receipts and the existing atomic
publication journal apply. Unknown or interrupted residues are not adopted by
identity cleanup. Keep-data retains records and originals; full cleanup removes
matching enrolled identities. Linked/hard-linked or corrupted snapshots are
refused. General Agent file tools can write only outbox artifacts, not snapshots.

Ordinary publication uses an exclusive same-directory hard link to a completely
written, flushed temporary, then removes the temporary name. A foreign final name
appearing concurrently is preserved and registration fails. A hard exit between
link and unlink may retain two names for one inode; subsequent reads refuse that
hard-linked residue rather than claim it as an ordinary immutable snapshot. No
business row can commit before that unlink and digest verification complete.

`list_materials` returns metadata; `get_package(material_id)` verifies the blob
and returns original_text plus distinct summary (initially null). GUI renders
original text literally, separately from generated summaries. Material records
have no session/task foreign key: conversation lifecycle does not own originals.
This ticket adds no conversation-delete API, generated summarizer, web ingestion
or destructive material collection. Missing/corrupt originals are reported, not
regenerated from summaries. No public command mutates a published snapshot.
