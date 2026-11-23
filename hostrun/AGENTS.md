# Desktop bridge, Agent tasks, Voyager retrieval, local knowledge and Keeper decisions

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | Host-owned execution layer for horizon. |
| [agent_capacity.py](agent_capacity.py) | Process-local execution slots shared by all contained desktop workers. |
| [agent_delivery.py](agent_delivery.py) | Agent-owned durable delivery ledger; channels never own task state. |
| [agent_run.py](agent_run.py) | Run the bundled Horizon Pi agent with bounded execution, events and leases. |
| [agent_scheduling.py](agent_scheduling.py) | Public persistent schedule commands: explicit development scopes only. |
| [agent_service.py](agent_service.py) | Agent-owned persistent commands and queries for authenticated desktop clients. |
| [archive_index.py](archive_index.py) | The archive index: a code-directory ledger of the material files. |
| [audit.py](audit.py) | Existing-data audit: compare the cloud libraries with the migration baseline. |
| [base.py](base.py) | Config, Base-client factory and executable resolution for host runs. |
| [cleanup.py](cleanup.py) | Preparation of isolated test instances; existing installations are never adopted. |
| [cleanup_cli.py](cleanup_cli.py) | Independent Windows test-instance tool; complete kit acceptance remains pending. |
| [cli.py](cli.py) | hostrun CLI: the entry points a host agent calls. |
| [console.py](console.py) | Console shell: the single resident foreground program (ADR 0009 §1/§3). |
| [console_events.py](console_events.py) | The Console's Feishu event surface (ADR 0009 §1/§4). |
| [console_gate.py](console_gate.py) | The knowledge-base gate for the daily push (ADR 0009 §6, #87). |
| [console_init.py](console_init.py) | Console ``/horizon-init`` step machine (ADR 0009 §7). |
| [console_service.py](console_service.py) | Console service lifecycle: scheduling, pause/resume, control file and shutdown. |
| [context_versions.py](context_versions.py) | Stage-B read-only context version interface; no production knowledge claimed. |
| [desktop.py](desktop.py) | Single Electron desktop entry point; Python owns the process job. |
| [desktop_bridge.py](desktop_bridge.py) | Authenticated loopback bridge; Python owns the Windows job and GUI lifetime. |
| [desktop_processes.py](desktop_processes.py) | Windows desktop process boundary. Install before starting any worker. |
| [desktop_runtime.py](desktop_runtime.py) | Verify installed Electron files for managed launch and cache ordinary launch runtimes. |
| [desktop_upgrade.py](desktop_upgrade.py) | Controlled package upgrade after the desktop releases its data-home lock. |
| [desktop_worker.py](desktop_worker.py) | Headless console and agent worker for the TypeScript desktop. |
| [diagnostics.py](diagnostics.py) | Local, allowlisted diagnostics. Never copies configuration or conversation files. |
| [dist.py](dist.py) | Install a distribution package and keep the installation's lock and evidence. |
| [distribution.py](distribution.py) | Build the installable distribution package, its manifest and its digest. |
| [editor_preferences.py](editor_preferences.py) | Versioned writing styles and opt-in feedback suggestions. |
| [editor_reports.py](editor_reports.py) | Editor source context, report persistence and citation reading. |
| [editor_synthesis.py](editor_synthesis.py) | Question-led Editor context, coverage gaps and confirmed background. |
| [embedded_agent.py](embedded_agent.py) | Horizon-owned Pi runtime and model configuration (no external agent). |
| [executor.py](executor.py) | Single-executor lease: one host drives one knowledge base at a time. |
| [facilities.py](facilities.py) | Create or attach the Feishu facilities horizon needs, by confirmed names. |
| [feedback.py](feedback.py) | Persistent feedback in Feishu: questions, submissions, results, corrections. |
| [frontier.py](frontier.py) | Local Frontier (关注问题/方向) knowledge, independent of Feishu. |
| [initialize.py](initialize.py) | Formal knowledge initialization from literature, requirements and a draft. |
| [install.py](install.py) | Installation: capability probes, resumable progress, honest report. |
| [intent_judgement.py](intent_judgement.py) | Intent judgement and the one-time confirmation card (ADR 0009 §4, #83). |
| [intents.py](intents.py) | Bounded host runs: one intent, one process, then exit. |
| [kb.py](kb.py) | Knowledge base: create an empty one, or attach an existing one by link. |
| [keeper.py](keeper.py) | Persistent Keeper requests and read-only, reviewable knowledge proposals. |
| [keeper_decisions.py](keeper_decisions.py) | Decision planning for one whole-group Keeper approval or rejection. |
| [keeper_evidence_policy.py](keeper_evidence_policy.py) | K7 rejection and duplicate policy for Keeper proposals. |
| [keeper_group_undo.py](keeper_group_undo.py) | Whole-group undo of one approved Keeper proposal (K9 #213). |
| [keeper_merge.py](keeper_merge.py) | Pure K9 helpers for merging one Knowledge Card into another. |
| [keeper_revisions.py](keeper_revisions.py) | Revision planning for one Keeper proposal after real owner drift. |
| [keeper_runtime.py](keeper_runtime.py) | Trusted host dispatch for an independent, bounded Keeper review child. |
| [keeper_worker.py](keeper_worker.py) | Deterministic, offline Keeper reviewer. |
| [knowledge_cards.py](knowledge_cards.py) | Local Knowledge Cards (知识卡片), independent of Feishu. |
| [knowledge_history.py](knowledge_history.py) | Cross-owner knowledge history, undo preview and non-covering undo. |
| [knowledge_themes.py](knowledge_themes.py) | Local Themes (命题) with applicability conditions and directional evidence. |
| [knowledge_workbench.py](knowledge_workbench.py) | Read-only search across the local Knowledge Workbench. |
| [lark.py](lark.py) | Lark CLI: discover or privately install it, then check identity and access. |
| [launcher.py](launcher.py) | Pure generators for the launchers an installation runs. |
| [layout.py](layout.py) | One resolution of the two top-level locations: code directory and archive. |
| [managed_artifact.py](managed_artifact.py) | Creation receipts for existing write_file outputs; updates are not atomic. |
| [managed_database.py](managed_database.py) | SQLite connections with creation-time identities for managed test instances. |
| [managed_desktop.py](managed_desktop.py) | Owned desktop lock and one-shot activation signals for managed instances. |
| [managed_diagnostics.py](managed_diagnostics.py) | Creation-time identities for the two fixed managed diagnostic log files. |
| [managed_import.py](managed_import.py) | Identity receipts for local material copies and their business ledger. |
| [managed_install.py](managed_install.py) | First installation with creation-time receipts; legacy residue is never adopted. |
| [managed_shortcut.py](managed_shortcut.py) | One creation-enrolled shortcut inside a committed test application. |
| [managed_upgrade.py](managed_upgrade.py) | Identity-enrolled code upgrades; data migrations require a separate transaction. |
| [managed_upgrade_data.py](managed_upgrade_data.py) | Enrolled data snapshots used by managed upgrade and recovery. |
| [managed_upgrade_initialize.py](managed_upgrade_initialize.py) | Run a verified target package's explicit SQL schema entry in the tool runtime. |
| [managed_upgrade_operation.py](managed_upgrade_operation.py) | Managed upgrade coordination and explicit recovery through enrolled identities. |
| [managed_upgrade_scope.py](managed_upgrade_scope.py) | Private same-thread nesting of one managed upgrade's exclusive native lease. |
| [migration.py](migration.py) | One-time migration from the legacy single data root to code + archive. |
| [model_catalog.py](model_catalog.py) | Authenticated provider model discovery; credentials never leave backend state. |
| [model_config.py](model_config.py) | Private pipe endpoint for the bundled Node agent's configuration reader. |
| [operations.py](operations.py) | The two whitelists: the operation registry and the instruction whitelist. |
| [pdf_worker.py](pdf_worker.py) | Bounded PDF extraction child; never returns executable PDF content. |
| [pi_session.py](pi_session.py) | Bounded pipe guardian for one managed Pi session; EOF releases all handles. |
| [release.py](release.py) | Release identity, the release index and artifact checks (spec #92, ticket #98). |
| [runtime_inventory.py](runtime_inventory.py) | Deterministic inventory of the runtime bytes and dependency metadata shipped. |
| [schedule_runtime.py](schedule_runtime.py) | Desktop scheduler runtime. Persistent claims own task identity; workers own processes. |
| [update_kb.py](update_kb.py) | ``/horizon-update-kb`` read side: copy, shard, read and report (ADR 0009 §8). |
| [update_kb_review.py](update_kb_review.py) | ``/horizon-update-kb`` review side: one key per candidate, then write (ADR 0009 §8). |
| [voyager_child.py](voyager_child.py) | The read-only evidence supplied to a Voyager child session. |
| [voyager_doi_sources.py](voyager_doi_sources.py) | Voyager discovery of DOI records. |
| [voyager_fulltext.py](voyager_fulltext.py) | Resolve and snapshot open academic full texts for reserved Voyager candidates. |
| [voyager_late_fulltext.py](voyager_late_fulltext.py) | Coverage-neutral follow-up work for a scheduled task's late full text. |
| [voyager_material_lifecycle.py](voyager_material_lifecycle.py) | Archive, immutable citation bindings and exact-snapshot reclamation. |
| [voyager_materials.py](voyager_materials.py) | Offline user-input materials and immutable, content-addressed text snapshots. |
| [voyager_pending.py](voyager_pending.py) | Resolve academic material waiting for a full text. |
| [voyager_relevance.py](voyager_relevance.py) | Explain Voyager selection from retrieved metadata and explicit scope terms. |
| [voyager_service.py](voyager_service.py) | Voyager-owned explicit scopes, retrieval budgets and durable public results. |
| [voyager_sources.py](voyager_sources.py) | Bounded academic discovery providers for the Voyager public service. |
| [voyager_subscriptions.py](voyager_subscriptions.py) | Persistent Voyager feed and bounded section subscriptions. |
| [voyager_tavily.py](voyager_tavily.py) | Free-plan-only Tavily discovery with durable precharged credits. |
| [voyager_web.py](voyager_web.py) | Public web page acquisition and immutable, paragraph-addressable extraction. |
| [voyager_web_renderer.py](voyager_web_renderer.py) | Package-only Chromium execution for public dynamic document capture. |
| [windows_cleanup.py](windows_cleanup.py) | Delete through pinned Windows handles, never a re-resolved pathname. |
| [windows_process_cleanup.py](windows_process_cleanup.py) | Exact Windows process handles for stopping an enrolled desktop owner. |
| [windows_protection.py](windows_protection.py) | Current-user Windows DPAPI integrity protection; never enables machine scope. |
| [windows_shortcut.py](windows_shortcut.py) | Serialize a native ShellLink to memory without creating a pathname. |
| [workspace.py](workspace.py) | Installation workspace: code, data root, private uv/Python and the run skill. |

## Verification

For Voyager, follow [VoyagerService](voyager_service.py) and run its public behavior tests from the checkout root with `python -X utf8 -m pytest tests/test_voyager_service.py` and a per-run temporary directory under `tmp/`.
