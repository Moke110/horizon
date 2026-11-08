# Local diagnostic reports

Diagnostics are local support evidence, not business state or telemetry. They never authorize a model call, retry, task transition or upload. Logging failure must not block application work or recovery.

The data home holds `diagnostics/events.jsonl` and one rotated `events.previous.jsonl`, each targeted at 256 KiB. Events contain `schema_version: 1`, a UTC ISO-8601 time, an allowlisted event name and bounded scalar metadata. Supported events cover desktop start/exit, main and child task start/finish, explicit task transitions, retries/errors, upgrade/backup/schema migration/recovery and diagnostic export. Values identify states, IDs, attempt numbers, process IDs and stable error codes; arbitrary error messages, request bodies, headers and environment dumps are excluded.

Upgrade rollback preserves the diagnostics directory and excludes it from business-state backups, so a failed migration does not erase its evidence. An abrupt process termination may leave a final event absent. Rotated or unavailable logs must not be described as a complete history.

The authenticated desktop `export_diagnostics` command works without a configured model and creates a uniquely named directory under `diagnostics/reports/`. The offline command is `python -m hostrun.diagnostics --code <installation> --output <directory>` with optional `--home <data-home>`. Offline inspection opens an existing SQLite database read-only and does not initialize or migrate it. It limits its snapshot to recent safe state metadata.

Each exported directory contains `summary.md`, `events.jsonl`, `system.json`, `tasks.json` and `build.json`. JSON documents have a top-level schema version. Exports omit model configuration, API keys, authorization headers, conversation/document bodies, task titles, model result text, database files and Pi transcripts. No arbitrary directory is recursively collected. Diagnostic export itself sends nothing over the network; the user chooses whether to share the report.

The optional Windows installation test kit invokes each setup with Inno `/LOG` and records exit codes. Its collector places installer logs, a system/build summary and application reports under one `report/` folder. Installer logs may contain local installation paths. When the application runtime is absent, the kit records that its application export is unavailable and preserves installation logs for investigation. Collection does not change Windows security settings or PowerShell execution policy.
