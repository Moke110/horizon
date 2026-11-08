# Embedded Horizon Agent

Horizon owns the Agent lifecycle. The TypeScript implementation in `agent/`
embeds the pinned Pi SDK; no DSH executable, external Pi installation or host
skill registration is required. Existing Python modules retain business rules,
validation, storage, rendering and PDF extraction.

The console launches the packaged `runtime/node.exe` and `agent/dist/main.js`.
Source checkouts may resolve Node from PATH. The console passes the runtime
directory and task kind, and supplies its own Python interpreter through
`HORIZON_PYTHON`. Model keys never appear in argv. Pi resources are isolated
from global Pi settings and development AGENTS.md.

Configuration is local at `.horizon/agent/model.json`: `provider`, `model`,
`apiKey`, and optional `baseUrl` for an OpenAI-compatible endpoint. It is a
credential file, not an artifact or an installation-report field. Protect it
with the same account permissions as other local credentials.

Tool policies:

| Task kind | Tools |
|---|---|
| `intent_judgement` | none; JSON classification only |
| `kb_read` | `read_file`; extracted source text only, no mutations |
| other business runs | `read_file`, `write_file`, `horizon_cli` |

`horizon_cli` spawns an allowlisted business module with argv, without a shell.
The Python validators and user-review gates remain authoritative. File tools
reject credential directories and writes outside runtime/archive directories.
This is an application tool boundary, not an operating-system sandbox.

The NDJSON stream contains `session`, `text`, `tool_call`, `tool_result`, `final`,
`status` and `error`. The existing runner keeps logs, cancellation, timeouts,
single-flight locking and executor leases. Intent judgement and knowledge reads
retain their existing separate concurrency behavior. Pi automatic retries are disabled.

Distribution builds include Pi's production dependencies, Node.js, the Feishu
CLI binary and the existing Python runtime. Historical package manifests may
still contain `compat.dsh` for reading old packages; the new console never launches DSH.
