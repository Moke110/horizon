# Behavioral, compatibility and integration checks

| File | Responsibility |
|---|---|
| [conftest.py](conftest.py) | Defines `cli_locations`, `kl_home`, `fixtures_dir`, `offline_home`. |
| [keeper-proposal.test.cjs](keeper-proposal.test.cjs) | Node tests for Keeper proposal and undo rendering. |
| [native_registration.py](native_registration.py) | Fault injection at the real Windows registration publication boundary. |
| [test_agent_delivery.py](test_agent_delivery.py) | Behavioral checks for agent delivery. |
| [test_agent_retry.py](test_agent_retry.py) | Behavioral checks for agent retry. |
| [test_agent_scheduling.py](test_agent_scheduling.py) | Behavioral checks for agent scheduling. |
| [test_agent_service.py](test_agent_service.py) | Behavioral checks for agent service. |
| [test_agent_tasks.py](test_agent_tasks.py) | Behavioral checks for agent tasks. |
| [test_archive.py](test_archive.py) | Behavioral checks for archive. |
| [test_archive_reports.py](test_archive_reports.py) | Behavioral checks for Newsroom drafts, Librarian confirmation and immutable Archive reports. |
| [test_bitable.py](test_bitable.py) | Behavioral checks for bitable. |
| [test_cleanup_cli.py](test_cleanup_cli.py) | Behavioral checks for cleanup cli. |
| [test_cleanup_instance.py](test_cleanup_instance.py) | Behavioral checks for cleanup instance. |
| [test_cleanup_tool_build.py](test_cleanup_tool_build.py) | Behavioral checks for cleanup tool build. |
| [test_desktop.py](test_desktop.py) | Behavioral checks for desktop. |
| [test_desktop_processes.py](test_desktop_processes.py) | Behavioral checks for desktop processes. |
| [test_desktop_runtime.py](test_desktop_runtime.py) | Behavioral checks for desktop runtime. |
| [test_desktop_upgrade.py](test_desktop_upgrade.py) | Behavioral checks for desktop upgrade. |
| [test_diagnostics.py](test_diagnostics.py) | Behavioral checks for diagnostics. |
| [test_editor_keeper.py](test_editor_keeper.py) | Production Editor-to-Keeper authority and Explorer approval. |
| [test_editor_preferences.py](test_editor_preferences.py) | Writing style precedence and feedback adoption. |
| [test_editor_reports.py](test_editor_reports.py) | Editor report persistence and original source reading. |
| [test_dist_e2e.py](test_dist_e2e.py) | Behavioral checks for dist e2e. |
| [test_dist_install.py](test_dist_install.py) | Behavioral checks for dist install. |
| [test_dist_pending.py](test_dist_pending.py) | Behavioral checks for dist pending. |
| [test_dist_releases.py](test_dist_releases.py) | Behavioral checks for dist releases. |
| [test_dist_source.py](test_dist_source.py) | Behavioral checks for dist source. |
| [test_dist_wrapper.py](test_dist_wrapper.py) | Behavioral checks for dist wrapper. |
| [test_distribution_docs.py](test_distribution_docs.py) | Behavioral checks for distribution docs. |
| [test_embedded_agent.py](test_embedded_agent.py) | Behavioral checks for embedded agent. |
| [test_frontier.py](test_frontier.py) | Behavioral checks for frontier. |
| [test_horizon.py](test_horizon.py) | Behavioral checks for horizon. |
| [test_hostrun_agent_run.py](test_hostrun_agent_run.py) | Behavioral checks for hostrun agent run. |
| [test_hostrun_base.py](test_hostrun_base.py) | Behavioral checks for hostrun base. |
| [test_hostrun_cli.py](test_hostrun_cli.py) | Behavioral checks for hostrun cli. |
| [test_hostrun_console.py](test_hostrun_console.py) | Behavioral checks for hostrun console. |
| [test_hostrun_console_events.py](test_hostrun_console_events.py) | Behavioral checks for hostrun console events. |
| [test_hostrun_console_init.py](test_hostrun_console_init.py) | Behavioral checks for hostrun console init. |
| [test_hostrun_console_init_completion.py](test_hostrun_console_init_completion.py) | Behavioral checks for hostrun console init completion. |
| [test_hostrun_console_init_lark.py](test_hostrun_console_init_lark.py) | Behavioral checks for hostrun console init lark. |
| [test_hostrun_console_init_transport.py](test_hostrun_console_init_transport.py) | Behavioral checks for hostrun console init transport. |
| [test_hostrun_console_service.py](test_hostrun_console_service.py) | Behavioral checks for hostrun console service. |
| [test_hostrun_distribution.py](test_hostrun_distribution.py) | Behavioral checks for hostrun distribution. |
| [test_hostrun_facilities.py](test_hostrun_facilities.py) | Behavioral checks for hostrun facilities. |
| [test_hostrun_feedback.py](test_hostrun_feedback.py) | Behavioral checks for hostrun feedback. |
| [test_hostrun_initialize.py](test_hostrun_initialize.py) | Behavioral checks for hostrun initialize. |
| [test_hostrun_install.py](test_hostrun_install.py) | Behavioral checks for hostrun install. |
| [test_hostrun_intent_judgement.py](test_hostrun_intent_judgement.py) | Behavioral checks for hostrun intent judgement. |
| [test_hostrun_intents.py](test_hostrun_intents.py) | Behavioral checks for hostrun intents. |
| [test_hostrun_kb.py](test_hostrun_kb.py) | Behavioral checks for hostrun kb. |
| [test_hostrun_lark.py](test_hostrun_lark.py) | Behavioral checks for hostrun lark. |
| [test_hostrun_layout.py](test_hostrun_layout.py) | Behavioral checks for hostrun layout. |
| [test_hostrun_migrate.py](test_hostrun_migrate.py) | Behavioral checks for hostrun migrate. |
| [test_hostrun_operations.py](test_hostrun_operations.py) | Behavioral checks for hostrun operations. |
| [test_hostrun_runtime.py](test_hostrun_runtime.py) | Behavioral checks for hostrun runtime. |
| [test_hostrun_update_kb.py](test_hostrun_update_kb.py) | Behavioral checks for hostrun update kb. |
| [test_hostrun_update_kb_review.py](test_hostrun_update_kb_review.py) | Behavioral checks for hostrun update kb review. |
| [test_hostrun_workspace.py](test_hostrun_workspace.py) | Behavioral checks for hostrun workspace. |
| [test_initial_build.py](test_initial_build.py) | Behavioral checks for initial build. |
| [test_keeper.py](test_keeper.py) | Behavioral checks for keeper. |
| [test_keeper_bridge_decide.py](test_keeper_bridge_decide.py) | Behavioral checks for keeper bridge decide. |
| [test_keeper_decisions.py](test_keeper_decisions.py) | Behavioral checks for keeper decisions. |
| [test_keeper_evidence_policy.py](test_keeper_evidence_policy.py) | Behavioral checks for keeper evidence policy. |
| [test_keeper_group_undo.py](test_keeper_group_undo.py) | Behavioral checks for keeper group undo. |
| [test_keeper_merge.py](test_keeper_merge.py) | Behavioral checks for keeper merge. |
| [test_keeper_revisions.py](test_keeper_revisions.py) | Behavioral checks for keeper revisions. |
| [test_keeper_runtime.py](test_keeper_runtime.py) | Behavioral checks for keeper runtime. |
| [test_knowledge_cards.py](test_knowledge_cards.py) | Behavioral checks for knowledge cards. |
| [test_knowledge_history.py](test_knowledge_history.py) | Behavioral checks for knowledge history. |
| [test_knowledge_themes.py](test_knowledge_themes.py) | Behavioral checks for knowledge themes. |
| [test_knowledge_transaction.py](test_knowledge_transaction.py) | Behavioral checks for knowledge transaction. |
| [test_knowledge_workbench.py](test_knowledge_workbench.py) | Behavioral checks for knowledge workbench. |
| [test_lark_utf8_decoding.py](test_lark_utf8_decoding.py) | Behavioral checks for lark utf8 decoding. |
| [test_lark_adapter.py](test_lark_adapter.py) | Pinned Feishu installation, isolation and diagnostic checks. |
| [test_library.py](test_library.py) | Behavioral checks for library. |
| [test_mac_lifecycle_probe.py](test_mac_lifecycle_probe.py) | Behavioral checks for mac lifecycle probe. |
| [test_mac_supervision_prototype.py](test_mac_supervision_prototype.py) | Behavioral checks for mac supervision prototype. |
| [test_managed_artifact.py](test_managed_artifact.py) | Behavioral checks for managed artifact. |
| [test_managed_import.py](test_managed_import.py) | Behavioral checks for managed import. |
| [test_managed_shortcut.py](test_managed_shortcut.py) | Behavioral checks for managed shortcut. |
| [test_model_catalog.py](test_model_catalog.py) | Behavioral checks for model catalog. |
| [test_pdf_ingest.py](test_pdf_ingest.py) | Behavioral checks for pdf ingest. |
| [test_pdf_pypdf.py](test_pdf_pypdf.py) | Behavioral checks for pdf pypdf. |
| [test_pdf_writer.py](test_pdf_writer.py) | Behavioral checks for pdf writer. |
| [test_pipeline.py](test_pipeline.py) | Behavioral checks for pipeline. |
| [test_publisher.py](test_publisher.py) | Behavioral checks for publisher. |
| [test_publisher_feishu.py](test_publisher_feishu.py) | Behavioral checks for publisher feishu. |
| [test_queue.py](test_queue.py) | Behavioral checks for queue. |
| [test_registration_io.py](test_registration_io.py) | Behavioral checks for registration io. |
| [test_release_ci_smoke.py](test_release_ci_smoke.py) | Behavioral checks for release ci smoke. |
| [test_release_pipeline.py](test_release_pipeline.py) | Behavioral checks for release pipeline. |
| [test_release_sources.py](test_release_sources.py) | Behavioral checks for release sources. |
| [test_retriever.py](test_retriever.py) | Behavioral checks for retriever. |
| [test_review.py](test_review.py) | Behavioral checks for review. |
| [test_runtime_inventory.py](test_runtime_inventory.py) | Behavioral checks for runtime inventory. |
| [test_theme_inventory.py](test_theme_inventory.py) | Behavioral checks for theme inventory. |
| [test_trigger.py](test_trigger.py) | Behavioral checks for trigger. |
| [test_under_review.py](test_under_review.py) | Behavioral checks for under review. |
| [test_update_cards.py](test_update_cards.py) | Behavioral checks for update cards. |
| [test_voyager_archive.py](test_voyager_archive.py) | Behavioral checks for voyager archive. |
| [test_voyager_child.py](test_voyager_child.py) | Behavioral checks for voyager child. |
| [test_voyager_doi_sources.py](test_voyager_doi_sources.py) | Behavioral checks for voyager doi sources. |
| [test_voyager_fulltext.py](test_voyager_fulltext.py) | Behavioral checks for voyager fulltext. |
| [test_voyager_materials.py](test_voyager_materials.py) | Behavioral checks for voyager materials. |
| [test_voyager_pdf.py](test_voyager_pdf.py) | Behavioral checks for voyager pdf. |
| [test_voyager_pending.py](test_voyager_pending.py) | Behavioral checks for voyager pending. |
| [test_voyager_service.py](test_voyager_service.py) | Behavioral checks for voyager service. |
| [test_voyager_sources.py](test_voyager_sources.py) | Behavioral checks for voyager sources. |
| [test_voyager_subscriptions.py](test_voyager_subscriptions.py) | Behavioral checks for voyager subscriptions. |
| [test_voyager_tavily.py](test_voyager_tavily.py) | Behavioral checks for voyager tavily. |
| [test_voyager_web.py](test_voyager_web.py) | Behavioral checks for voyager web. |
| [test_voyager_web_renderer.py](test_voyager_web_renderer.py) | Behavioral checks for voyager web renderer. |
| [test_windows_acceptance.py](test_windows_acceptance.py) | Behavioral checks for windows acceptance. |
| [test_writer_proposals.py](test_writer_proposals.py) | Behavioral checks for writer proposals. |

## Verification

Use the test file for the affected subsystem. For example, `test_voyager_service.py` exercises the Voyager service through its public API.
