# Embedded Horizon Agent

Horizon owns the Agent lifecycle. The TypeScript implementation in `agent/`
embeds the pinned Pi SDK and loads `skills/horizon-run/SKILL.md` from the Horizon code directory. Python modules retain business rules, validation, storage, rendering and PDF extraction.

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
| Legacy business runs without a persisted desktop turn ID | `read_file`, `write_file`, `horizon_cli`, `delegate` |
| Persisted desktop Explorer turn | `read_file`, `delegate`, `dictionary`, `hpedia_question`, `archive_source`, `frontier_tracking`, `library_test_cleanup` |
| Editor, Voyager and Keeper children | Only their own read-only context tool |

`horizon_cli` spawns an allowlisted business module with argv, without a shell.
The Python validators and user-review gates remain authoritative. File tools
reject credential directories and writes outside the home/outbox directory.
This is an application tool boundary, not an operating-system sandbox.

For managed Windows test homes, `write_file` sends the request through stdin to
the fixed Python `-B` artifact bridge, which registers newly created outbox
identities before writing. Unknown or replaced entries are refused. Existing
owned writes are in-place and may be partial on failure; success requires a full
write and flush. Ordinary homes retain their existing writer. See the
[cleanup contract](windows-test-cleanup.md) for identity and report boundaries.

The Library tools use the owning Python desktop session and stable request IDs. Reads return committed records. Write tools return a review card in the current Explorer conversation and do not apply changes before confirmation. A settle operation can return a stable failure receipt when a reviewed change no longer applies. The Agent may not confirm its own cards. Use `testData: true` when creating development test content, and use `library_test_cleanup` to preview marked records and request a confirmed cleanup.

The NDJSON stream contains `session`, `text`, `tool_call`, `tool_result`, `final`,
`status` and `error`. The existing runner keeps logs, cancellation, timeouts,
single-flight locking and executor leases. Intent judgement and knowledge reads
retain their existing separate concurrency behavior. Pi automatic retries are disabled.

Distribution builds include Pi's production dependencies, Node.js, the Feishu
CLI binary, the embedded Agent’s runtime skill and the Python runtime. Older packages may carry historical compatibility metadata. Installation checks apply the OS, architecture and Python requirements.
