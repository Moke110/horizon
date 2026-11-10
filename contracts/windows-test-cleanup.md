# Windows test-instance cleanup primitives

Test instances use `%LOCALAPPDATA%/Horizon/test-instances/<instance-id>` with separate `app`, `data` and `archive` children. Existing installations are not adopted. Registration and lifecycle locks live outside these removable children. Ordinary data homes outside this controlled root retain their existing lifecycle.

Current-user Windows DPAPI protects registration integrity and binds it to the registration path. The record contains volume/file identities for the instance and enrolled children. Missing, altered or replaced identities refuse startup before writing the data home. This protects against corrupted or substituted records; it does not claim protection from a malicious process with the same user's DPAPI authority.

Desktop and upgrade hold shared lifecycle leases; cleanup requests an exclusive lease. The existing desktop lock still controls window activation. Lease and identity failures exit without creating diagnostic files in the blocked home.

Initialization requires Windows 10 or later. `NtCreateFile` uses `FILE_CREATE` and a single-component name relative to a held parent directory handle. Newly created staging directories and children remain held through enrollment. A protected journal precedes child moves through handle-based rename. Recovery verifies enrolled identities; unknown staging residues are retained and identified by hashes in independent reports.

Cleanup requires explicit instance-ID confirmation and successful independent allowlisted diagnostic export outside the controlled root. Deletion pins ancestors, rejects reparse targets and uses `SetFileInformationByHandle(FileDispositionInfo)` on the opened target. It never recursively deletes an arbitrary recorded path. Completed deletions remain reported if a later item fails. Unknown content is preserved and prevents a clean result.

The deletion inventory currently comprises only enrolled data-home `model-config.json`, `horizon.sqlite3`, its WAL/SHM files and `version.json`. Application files, directories and other content are retained. This primitive is not a complete uninstall command or a deliverable cleanup kit. Remaining acceptance is tracked in GitHub issue #230 and `docs/acceptance/README.md`.
