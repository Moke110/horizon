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
Material records and citation identities do not cascade with a conversation or
Frontier deletion. Missing/corrupt originals are reported, not
regenerated from summaries. No public command mutates a published snapshot.

Desktop PDF import uses the native file picker and accepts at most 10 MiB.
The material stores separate digests for the immutable original PDF and the
extracted text. One-based anchors identify PDF pages and half-open Unicode
code-point offsets in the extracted text. Supplemental text has no automatic
page mapping. The sandboxed renderer uses the bundled PDF.js worker and local
fonts. The desktop protocol exposes only the currently selected authenticated
material's original bytes and an allowlist of application assets. Reader
navigation accepts page numbers, never an arbitrary filesystem path.

For a public HTTPS page, `register_web` accepts an operation ID and URL and
returns a material whose web status records availability or the failure
reason. Static acquisition uses Trafilatura to extract article text and
source-supplied title, author, publication date and site name where present.
An available snapshot records original-byte and extracted-text SHA-256 hashes.
The paragraph IDs and half-open Unicode code-point spans refer to its saved
text. Opening a saved material reads the verified local snapshot.

For URL and candidate capture, `dynamic: true` renders public JavaScript content
with the Windows package's pinned Playwright Chromium. The saved original-byte
hash identifies the rendered HTML in dynamic mode. Browser rendering uses a
fresh profile, blocks navigation to a different top-level source, and passes
network fetches through the public HTTPS boundary. The capture worker owns a
nested Windows Job and uses a capture-scoped temporary directory.

For an academic Voyager candidate, `voyager_fulltext` reserves one acquisition
from the active segment, then checks source-native documents, OpenAlex OA
metadata, Unpaywall by DOI when configured, and the public landing page.
Each attempted stage records its outcome and response status. Unpaywall contact
details stay in private source settings and are omitted from material records.
Academic PDFs use the same bounded worker as manual imports. JATS XML uses the
article body and accepts standard DOCTYPE declarations without fetching DTDs
or resolving entities. Academic HTML must show evidence of an article body.

An available result records separate content and original digests. A failed
capture creates a `pending_fulltext` material with its attempt trail and empty
body. For the same DOI or source work ID, repeated extracted text merges provenance
without mutating its snapshot. Changed text or a distinct publisher notice
creates a version. Explicit publisher DOI relationships and arXiv revision
identifiers are retained, and citation counts can exceed the supplied IDs.
Both an available and a pending material can be inspected from the Voyager
receipt. Replaying the same acquisition returns its stored result. An
interrupted reservation with an unknown remote outcome stays charged.

### 待全文资料

资料页保留原始来源和失败记录。探索者可补充可读取的 PDF 或放弃该资料。补充登记同一资料实体的新快照版本，旧快照保持可读。放弃保留出处并停止对同一检索候选的再次全文获取。

摘要授权需显式确认并绑定原检索、资料和任务。读回时只向该任务呈现授权摘要及「未核对全文」限制。待全文及授权摘要均不构成正式报道的正文证据。

正式计划原任务的迟到全文进入该计划的持久串行队列。计划仍需处于启用状态，且没有当前计划执行或未完成的原任务对话；上传不会恢复暂停的计划或任务。续接在原任务新建独立对话轮次，不改变计划覆盖进度，也不自动重发报道。显式临时检索缺少持久计划绑定时，补充只登记资料。

## Archive, citations and snapshot reclamation

`archive_material(material_id)` removes the record from active lists. The
archived list is returned by `list_summaries(archived=True)` and
`list_materials(archived=True)`. Archived packages and citation text remain
readable. `register_reference(material_id, owner_id, content_hash)` verifies the
expected current snapshot and binds an immutable report citation owner to that
version. `resolve_reference(material_id, owner_id)` reads the verified referenced
text and returns a stable removed marker after permanent deletion. Both use the
authenticated service seam.

Permanent deletion requires an archived material and the exact
`deletion_impact` confirmation, which binds its current snapshot, affected
citation owners and associated retrieval IDs. Changes invalidate that
confirmation. Deletion preserves the material ID and citation tombstones while
discarding material data and its replay payloads. Academic reimports retain
the entity ID and continue numbering above every removed or surviving version.

`collect_orphan_snapshots` removes only validated content-addressed text or
original blobs absent from live materials, their version receipts, and live
citation bindings. Publishers and collection share a per-home cross-process
lock across blob publication and SQLite commit. Managed storage verifies each
file against its enrolled identity before collection. The service leaves
unrecognized or damaged entries untouched.
