# Desktop acceptance

The source-checkout launcher opens `hostrun.desktop` using pythonw. Native Tk widgets integrate the existing Console reader/output seams and the embedded Pi event stream. No release tag was published; packaged CPython still needs Tkinter before desktop distribution can be supported.

Validation: 49 focused tests passed, including native Tk widget creation, session persistence, streamed event rendering, sidebar toggling, Console initialization and embedded Pi integration. The sandbox full suite had 923 passed, 82 failed, 3 skipped; failures include missing platform architecture in distribution compatibility checks. The unrestricted Windows full suite subsequently passed: 1007 passed, 3 skipped, with two cache-write warnings.

Live acceptance remains pending: automated window inventory did not expose the launched Horizon window. Actual model-provider conversation and interactive Feishu workflows were not exercised. Do not interpret source deployment or focused tests as live acceptance.


Startup recovery: removed the two confirmed desktop test processes left running on an inaccessible desktop. The Explorer-launched window was observed through Windows UI Automation as `Horizon — Research Agent`. Repeated launches now request deiconify/lift/focus via an activation file; a Tk regression test verifies restoration from a withdrawn window. Actual model-provider and Feishu acceptance remain pending.

## Windows desktop process lifecycle — 2026-11-07

- Terminated the identified pre-existing Horizon GUI tree (34728 / 36608 and children), plus all diagnostic GUI instances created during this change.
- Installed local Horizon.lnk entries in checkout, parent folder and current-user Start Menu. They target pythonw directly, with no shell wrapper. No global security policy was changed; file-level Unblock-File applied only to launcher/runtime targets.
- 52 focused tests passed: desktop, real Windows normal/forced job cleanup, child GetConsoleWindow == 0, Console, initialization and embedded Agent. Pytest cache emitted one permissions warning.
- Interactive desktop shortcut launch/close acceptance remains pending: Explorer automation had unreliable focus; no successful visible GUI launch was observed in this verification. Do not treat process startup alone as visible GUI acceptance.
- Native .cmd is retained for CLI compatibility and may flash its own console; the supported zero-console entry is Horizon.lnk / Start Menu Horizon.


### TypeScript desktop migration — 2026-11-07

React/Electron assets build and typecheck. The desktop worker, process ownership, agent, distribution and release checks pass (102 tests). The Tkinter desktop and preview routing have been removed. Only `<code>/Horizon.lnk` is generated; outer and Start Menu shortcuts were removed from the local installation.

Live acceptance remains blocked: Electron 45.2.3 aborts before application JavaScript with `0x80000003`. Native debugging captured sandbox initialization return value 12 (`SBOX_ERROR_CANNOT_CREATE_WINSTATION`); both direct and desktop-helper launch failed. No sandbox or Windows security settings were changed. Rendering, live model interaction, and complete GUI shutdown acceptance remain unverified. Resident business services now wait for the renderer ready handshake.

### Electron startup repair — 2026-11-07

The earlier startup blocker is resolved locally. The same Electron 45.2.3 payload failed with 0x80000003 on the E: workspace volume but passed normal sandboxed startup from the user's C: local application directory. The launcher now atomically caches the bundled runtime under LOCALAPPDATA/Horizon/desktop-runtime, keyed by all runtime file contents. No sandbox or Windows security policy is disabled.

Two additional startup defects were fixed: a console-free Electron process treated stdin as EOF and quit before displaying its window; the generic child policy applied SW_HIDE to the GUI. The bridge now uses a per-launch token-authenticated loopback socket, and the visible GUI child retains CREATE_NO_WINDOW and Windows job containment without SW_HIDE.

Live checks: the outer `启动 Horizon.lnk` opened a visible Horizon window, and launching it twice retained the same main-window PID. Accessibility inspection confirmed the welcome screen and controls; an Electron renderer smoke test exercised settings, cancel and Console and captured `.horizon/smoke.png`. Alt+F4 closed the actual shortcut-launched GUI, and a subsequent process inspection found no Horizon Electron or desktop Python processes. Nine focused desktop tests passed, including real normal/forced Windows job cleanup. Live provider and Feishu acceptance remain outside this startup repair.

Remote fetch was rejected by automatic approval review; this repair remains local and has not been pushed.

Extended verification: 77 related tests passed together; the remaining real-checkout distribution test passed separately using a shorter temporary path (the initial failure exceeded Windows path limits). Desktop typecheck and build both passed. A final visible GUI was reopened for the user.

Integration follow-up: the maintainer explicitly authorized merging and pushing to main. Fetch succeeded with dev and origin/main at the same baseline. Pre-merge desktop, release and documentation checks passed (99 tests), along with desktop typecheck and build.

### Agent task service — 2026-11-08 ([#128](https://github.com/Moke110/horizon/issues/128))

The authenticated desktop bridge now uses the Agent application service and a stable, installation-independent SQLite home. Task results, operation receipts and events commit together. Review fixes restrict generic file writes to outbox, require operation IDs and expected versions on external sends, and reload state while restoring input after a command conflict.

Validation: 26 related tests and 8 desktop tests passed; Agent and desktop TypeScript builds/typechecks passed. Two distribution builds produced SHA-256 `d3c5f8672083ae95eddabbefda503b156bd53f029d7ef55278a4e83b62cd2c72`; `ci_install_smoke` passed. Full suite: 1029 passed, 3 skipped, 1 warning in 231.52 seconds with PROCESSOR_ARCHITECTURE=AMD64 set only for the test process; the initial 81 distribution failures were caused by the missing environment value.

Live packaged GUI acceptance remains pending: computer-use window-state text and screenshot requests hung or timed out, preventing reliable visual/interactive verification. Diagnostic process cleanup found zero remaining task-owned processes. These package and offline checks do not establish live GUI acceptance; #128 remains open. No release tag was published.

Recovery follow-up: rebuilt the current source twice; both packages have SHA-256 `84f93fa9096a9878ff904378586b9c1b184a3ba933ebf70f15596353c02a6103`. All 17,557 package entries with corresponding source files matched byte for byte before this evidence update; `ci_install_smoke` passed. The dirty source correctly records `built_from_commit: unknown`. This supersedes the earlier package digest for the current implementation.

Resetting the Computer Use JavaScript session restored sky window enumeration. The current installed package exposed real window 197688, but screenshot acquisition hung again for 125.9 seconds. Creating a task, normal close/reopen persistence, and zero-console launch remain pending. The identified test owner PID 2144 and Electron PIDs 10708, 13444, 17012 and 2100 were precisely cleaned up; all were confirmed exited. The computer-use skill directs all Computer Use actions through node_repl and @oai/sky and supplies no alternative UI automation path. This is partial startup evidence, not completed GUI acceptance. #128 remains open; implementation changes remain uncommitted and unpushed, and no release tag was published.
### Packaged service and GUI recovery follow-up — 2026-11-08

Using the installed package's runtime/python.exe and hostrun.agent_service from the current smoke installation (artifact SHA-256 84f93fa9096a9878ff904378586b9c1b184a3ba933ebf70f15596353c02a6103), public-service checks passed against isolated temporary SQLite homes: wrong-token writes were rejected; before-commit fault injection left neither task nor event; after-commit fault injection preserved both; retries returned the original result without duplicate events. Message persistence and replay also passed after recreating the service with a new launch token. Both fault phases passed, exit code 0; temporary homes were removed. These are packaged service checks with exception injection, not GUI acceptance or abrupt-process-crash recovery evidence.

A separate GUI attempt used the isolated horizon-gui-128-recovery3 home. Window enumeration and lookup found Horizon window 590898, but the standalone activate_window call hung for 108.6 seconds and was interrupted before any window-state request or task creation. Cleanup stopped only the identified owner PID 22548; three seconds later the owner and Electron descendants 23948, 10668, 3060 and 17248 were all absent. This establishes descendant cleanup for this forced owner exit, not normal GUI shutdown or visual zero-console acceptance.

GUI task creation, normal close/reopen persistence and visual zero-console startup remain pending. #128 stays open with downstream blockers unchanged. Existing user changes are retained; nothing was committed or pushed and no release tag was published.

Recovery attempt 4 (2026-11-08): the installed package launched with isolated horizon-gui-128-recovery4 data. Window 395022 was enumerated and activation succeeded. Accessibility returned only empty containers; screenshot attempts failed with 'FrameArrived timed out: timed out waiting on channel' and, after fresh selection/activation, 'window capture timed out: timed out waiting on channel'. No task input was attempted. Owner PID 24900 was stopped; a subsequent process check found neither it, Electron PID 24688, nor children of 24688. GUI creation, normal close/reopen persistence and visual zero-console acceptance remain pending. Focused agent_service/desktop/embedded_agent tests passed (26 tests). No ticket was closed, commit pushed or tag published.

### Replay correction and renderer diagnosis — 2026-11-08

The bridge now selects the task_id returned by create_task on operation replay instead of always selecting the newest task. A regression through the authenticated socket failed before the fix and passed afterward: creating A/B then replaying A selects A without adding a task or event. The desktop, agent-service and embedded-agent checks passed together (26 passed in 11.90s); diff whitespace checks passed.

The existing installed-package Electron renderer smoke passed (exit 0): welcome, settings, cancel and Console. Electron capturePage produced a complete rendered interface, inspected at C:/Users/Chang/AppData/Local/Temp/horizon-render-diag/render.png. This narrows the earlier capture failure to the external Windows observation path; it does not establish live task creation, normal desktop close/reopen persistence or zero-console acceptance. The smoke used a diagnostic authenticated socket, not the production Python service. The diagnostic owner and Electron exited under Job containment. The installed package predates this replay correction and must be rebuilt before final acceptance. #128 remains open; no commit, push or release tag.

### User-assisted packaged task persistence — 2026-11-08

The user manually created one new conversation in the recovery5 isolated home and reported closing the window. The original owner PID 31248 was absent. Public service readback returned exactly one task (654010a4-d576-4c22-86be-c6a28e88ff77), one creation event and no messages. Reopening the same installed package and home with owner PID 25888 exposed window 395682; its live accessibility tree contained a saved New conversation button in the sidebar, absent before creation. This establishes user-assisted GUI task creation and close/reopen persistence for artifact 84f93fa9096a9878ff904378586b9c1b184a3ba933ebf70f15596353c02a6103.

The reopened verification owner was stopped after observation. Another pythonw PID 14744 was observed with a separate invocation and no direct children; its provenance is unconfirmed, so it was not terminated and no global zero-orphan claim is made. Visual zero-console acceptance and acceptance of a rebuilt package containing the subsequent replay-selection correction remain pending. #128 remains open.

### Rebuilt package and installation relocation — 2026-11-08

Two builds containing the replay-selection fix produced identical SHA-256 3c806aff8a9362fc726f3c6e8ee8e04eec95f2117526f0a0f4f30f7118c1f882 (dist-final1 and dist-final2 under the temporary horizon-package-128-prep directory). Clean installation into smoke-final passed ci_install_smoke including the minimal-PATH wrapper check. Real Windows normal/forced Job cleanup tests passed (2 tests); the child console probe returned zero. Pytest reported only a cache-directory permission warning.

The new installed code was launched with the existing recovery5 home (owner 14084). Live window 133268 exposed the smoke-final file URL and the saved conversation in its sidebar. This verifies GUI readback after changing installation directory to the rebuilt package. Visual zero-console startup observation and normal closure of this new launch remain pending user confirmation. The window is left open for that check. Existing user changes remain uncommitted; no push or tag.

### Rebuilt-package startup and normal close confirmation — 2026-11-08

The user explicitly confirmed that only the Horizon main window appeared, with no black console window, and then closed it normally. After closure, owner PID 14084 and its direct children were absent; a separate filtered process check found no electron.exe/pythonw.exe invocation for smoke-final. Public-service readback retained exactly one task and one event. This completes the user-observed zero-console startup and normal-close checks for the rebuilt artifact 3c806aff8a9362fc726f3c6e8ee8e04eec95f2117526f0a0f4f30f7118c1f882, alongside the previously observed same-home readback from the new installation directory. The earlier unrelated PID 14744 is outside this scoped cleanup claim.

These results supersede the pending visual startup/normal closure items for this launch. They do not establish later-ticket real Pi/model or Feishu acceptance. Repository review and integration remain outstanding; #128 is not closed by this evidence update. No commit, push or release tag was performed.

### Integration review — 2026-11-08

Standards and spec reviews found malformed command error handling and stale desktop documentation; both were corrected. Public-service regression tests now reject non-object commands, boolean schema versions and invalid task IDs with stable errors. English/Chinese guides and the confirmed SQLite ADR were synchronized. The rename review verified all six historical GitHub release assets: their original frontier filenames were restored in the index, with narrow reader compatibility while new builds remain horizon-named. Six regressions failed before and passed after the correction; release-related checks passed (102 tests). Desktop TypeScript typecheck and Agent TypeScript build passed.

The prior full suite passed 1029 tests with 3 skipped. The final expanded suite passed 1038 tests with 3 skipped; its one real-checkout packaging test exceeded the Windows path limit and is being rechecked under a shorter temporary path. Earlier live GUI evidence applies to the UI/persistence path; subsequent changes are input validation, release-index compatibility and documentation. No additional user UI confirmation is required for those changes.

The final real-checkout packaging test passed under the shorter path (1 passed in 57.39s). Combined final coverage is 1039 passed and 3 skipped; remaining warnings concern deprecated fitz imports and pytest cache permissions.

### Persistent Pi session implementation — 2026-11-08

Issue #129 now assigns each task a durable session identity and restores Pi context from its private per-session JSONL directory. The authenticated desktop bridge sends only the new message with that identity; configuration and session storage use the stable product data home. Existing task messages and versions survive the identity migration. SQLite remains authoritative for business commits.

The new service identity and migration tests failed before implementation and passed afterward. Public-service and authenticated socket checks passed (22 tests), including completed-task follow-up and duplicate-send suppression. Desktop process and existing agent-run regression checks passed (15 tests). The Pi runtime build and separate-process context/isolation test passed against the real SDK with a local provider substitute. Real model and GUI acceptance remains pending and is not replaced by those tests; #129 remains open until that evidence is recorded.

Review follow-up for #129: prior task messages initialize an empty Pi session once; subsequent turns do not duplicate them. Provider text and error messages are redacted before JSONL persistence. Streaming redaction retains possible key-prefix tails so split chunks cannot reconstruct a key in the GUI. Regression tests reproduced these failures before fixes. The complete suite passed 1043 tests with 3 skipped; the final streaming fix then passed all 12 embedded-agent tests and the TypeScript build. Warnings were deprecated fitz imports and pytest cache permissions. Package and real-model GUI verification are still pending.

### Provider discovery and review completion — 2026-11-08

The user requested provider selection and API-key model discovery during #129 acceptance. The desktop now offers provider choices, authenticated backend discovery, a conversation model selector and refresh. Failed discovery preserves configuration; queries run in the background with configuration mutation guards. Credentials are normalized before authentication and response filtering. DeepSeek, OpenAI, Groq, Mistral, OpenRouter and custom OpenAI-compatible services are supported; catalog visibility does not prove inference permission or quota.

Final affected checks passed: 36 tests, desktop typecheck/build and diff whitespace checks. Earlier #129 full-suite evidence remains 1043 passed and 3 skipped. Standards and spec reviews were completed and identified persistence redaction, history migration, split-stream redaction, normalized-key filtering and synchronous discovery defects were corrected.

Live evidence: the user configured DeepSeek through the root Horizon launcher. Authenticated discovery returned deepseek-flash and deepseek-v4-pro; the live GUI exposed the model-selection combobox. This verifies discovery and selector presentation, not completed real-model conversation, follow-up, restart recovery or new-task context isolation. Those acceptance items remain pending and #129 stays open. The earlier session-only d4/d5 packages matched SHA-256 6305ac1055ca4f2206b2d14a606c811f43fb7011623c7299078da90434a20ae6; they predate this provider-discovery addition. No release tag was published.

### Real-model continuation acceptance — 2026-11-08

The user confirmed GUI operation and model discovery. Live DeepSeek runs through AgentService and DesktopWorker preserved token A2-b6ffe72346b64747ad99c343f60d7c92 across the initial response, completed-turn follow-up and recreated service/worker with separate Node/Pi processes. Task 39f84793-9daa-438b-82d2-bfca6babf35b retained session 77aa2b32-d6f8-434c-92d2-e0568fb40627. A separate task used a different session and did not receive that context. SQLite held 6/2 messages respectively with matching events; duplicate operation replay returned the original result. Outputs, task records and both transcripts contained no configured credential.

The user then closed and reopened Horizon, selected the acceptance conversation and confirmed that a GUI follow-up returned the exact same token. This completes the previously pending real-model continuation, restart and isolation acceptance for #129. External computer-use capture remained unavailable; the GUI restart/follow-up evidence is explicitly user-observed. No release tag was published.

### Restricted child-session acceptance — 2026-11-08

Issue #130 persists parent/child session identities, development roles, bounded input/version, success/failure and stable artifact references through AgentService transactions and GUI trace cards. Children expose only developer_context and restricted reference reads. Review findings about completion versions and cross-turn tool-call ID collisions were corrected. Verification: 1052 passed, 3 skipped; Agent/desktop builds and desktop typecheck passed. The later read-only cache reuse fix passed 15 focused tests and review.

Installed-package live DeepSeek task c940343f-db64-41aa-aad2-85eb6b19568d dispatched two independent children with synthetic input. Both invoked developer_context, succeeded and returned distinct stable artifact references to the parent; credential scans and service-recreation readback passed. The user confirmed both successful child cards, parent/child identities, input versions and artifact references in the GUI, then closed/reopened the installed application and confirmed the cards remained.

Two builds from facfdfe match SHA-256 d02b8522c26e384463613bb21ad80595cc959651c5394fef22efb48888bc1b64. Clean install/minimal-PATH wrapper smoke passed, and all 17,730 manifest files matched in the user's normal extraction at E:/Horizon-acceptance. Local sandbox extraction had inherited Windows Low integrity labels, preventing Electron startup; normal user extraction resolved that environment limitation without changing ACLs, labels or security policy. Existing published runtime caches are now reused without requiring an installation write lock. Evidence was collected on Windows 10.0.19045; it does not establish #131's clean Windows 11 acceptance. No release tag was published.


### Recoverable turns and installer verification — 2026-11-08

Issue #132 now persists original pending Pi tool calls and owner operation receipts, resumes the same task, and refreshes unfinished-step interface snapshots while retaining completed results. Knowledge/preferences remain explicitly unavailable development_empty providers under the agreed Agent-stage boundary; this is not live knowledge integration. Standards/spec review findings were corrected. Integrated checks passed 1071 tests with 3 skipped; Agent/desktop builds and desktop typecheck passed. The subsequent upgrade-journal schema checks passed 9 tests.

Two packages from a0e2303 (0.0.0-local.131.2) match SHA-256 d790fe5060df374aff06b27ae59f0816e08d72cec056085977a751ca4dec30ee. All 17,736 manifest files matched in an isolated normal Temp extraction. Real DeepSeek execution used only that package's Python/Node/service, synthetic input and developer_context. After owner termination immediately following the first child commit, a new owner resumed task c6a9edf1-aec8-4ca5-b1d1-7b3fdc046913 to completion: one user message, one final answer and two successful children. The first child identity, transcript hash and v1 snapshots were unchanged; the second used v2. Scoped owner/Node process cleanup passed. The package GUI accessibility tree showed the restored conversation and both artifact references. After the user opened Trace, live accessibility confirmed execution completed, separate material-wait/proposal/delivery labels and both succeeded child cards. This completes the recovery GUI check; coordinate input itself remains unavailable in the automation environment.

Issue #131 provides fixed-version runtime/license inventory and an external-runtime installer/upgrader with backups and atomic versioned journals. Actual Windows 10 testing exposed unclosed SQLite backup handles and Windows long-path backup failures; fixes 7563812/e70e68e passed 77 related tests. Two clean-source builds of 0.0.0-local.131.4 from e70e68ed6257b9240395318ebfb9b7cf75812814 match SHA-256 d3b1a34f52f56b2144ce08e23887f2fbb3ea9fa2ba6ba0750f02e4baf0f38631. Compared with the live-recovery package, only the upgrader and acceptance document differ; Agent/GUI/model runtime files are unchanged.

The final package passed minimal-PATH installed-runtime checks: 131.1 to 131.4 upgrade, running-lock rejection, injected schema failure recovery, unchanged session/configuration, manifest and atomic version marker. Actual Inno installation, upgrade and uninstall passed on Windows 10; unknown files and the independent data home survived uninstall while owned program files were removed. Final setup SHA-256: 10d1d81e657110214e8ddffe3fd254b8f1a0c2c466fefc208f501b8a5ee14622. Baseline/final installers and concise steps were handed to the user for clean Windows 11 x64 live main/child conversation and upgrade-continuation acceptance. That requirement remains pending and #131 stays open. #132 was integrated in main b1b80bb and closed after the package/GUI evidence above. No release tag was published.


### Tray, bounded retry and offline diagnostics handoff — 2026-11-08

Issues #133/#135 are implemented and reviewed. Corrections limit cancellation to the selected task, serialize unfinished turns and acknowledge explicit Exit despite concurrent completion. Package 0.0.0-local.135 from 061d304 reproduced SHA-256 c781850a2215f860cb77b6befef033be67e0aad6cdc7bc67a3fba661f003011b; all 17,738 manifest entries matched. Real packaged Python/Node/Pi with a controlled local provider verified active main/child pause, zero new requests after restart while paused, same-child resume, cancellation and late-result rejection. Forced owner termination removed its descendants and preserved an unrelated sentinel. Controlled SDK HTTP 429/503 requests were bounded at three, authentication/quota at one; retry cancellation and failed-call identity recovery passed. Actual GUI tray/restore/explicit-exit observation and error-card interaction remain pending, so #133/#135 remain open.

The user deferred Windows 11 while preparing an offline VM and requested a consolidated diagnostic folder. Local logs now contain bounded allowlisted lifecycle/task/child/retry/upgrade metadata; exports use a read-only database snapshot and exclude configuration, credentials, titles, conversation/document bodies and Pi transcripts. Rollback preserves diagnostic evidence. Review corrected raw-event copying in the fallback collector and ensured Windows hard exit records its event first; malformed metadata cannot abort report creation. The final full suite passed 1099 tests with 3 skipped; the separately added Windows collector privacy/minimal-PATH regression also passed.

Clean-source diagnostic builds from 7baaefe: baseline 0.0.0-local.131.5 package SHA-256 32e74950ff2d80847da793a112c732f27a46af9b0b757272ac28668aaed229bd; two 0.0.0-local.131.6 builds matched 6c50701cdba1992ba4344df3d2a69f4aa935ec0076d3d1b5157d95755596cf71. Setup SHA-256 values are 21e9f7c3ad777ebeed3f388ed95d99d163832cbbd50a921ecf84acf297d75c39 (baseline) and c9b7d0d7c85afaaff9cf5fd021b5b54a525a0571a664ac37a2ef4b7dd4743dab (upgrade). Actual Windows 10 Inno install/upgrade, bundled-runtime report export and final uninstall passed (exit 0); program files/registration were removed and the independent data home retained: version 131.6, ten upgrade events, retained synthetic task and no seeded secret/title/body/result in reports. The numbered scripts and instructions are assembled under installer/output/Windows11-diagnostics; install/upgrade wrappers save Inno logs and the collector produces one report folder, including an installation-only fallback with no app runtime.

Clean Windows 11 and outstanding GUI acceptance remain pending. The user requested pausing after this delivery; no further issue work is started and no public release tag is published.

### Windows isolated cleanup — 2026-11-11

[#230](https://github.com/Moke110/horizon/issues/230) remains **open**. The current independent Windows tool supports prepare/install, preview, retained diagnostics, confirmed keep-data/full cleanup, compatible-schema upgrade and explicit interrupted-upgrade recovery. It bundles pinned CPython 3.13.7 and PyYAML 6.0.2, and runs with minimal PATH and invalid developer PYTHONHOME/PYTHONPATH. These are development results, not final-kit delivery or physical Windows 11 acceptance. The authoritative boundaries are in [the cleanup contract](../../contracts/windows-test-cleanup.md); intermediate results remain in the issue and Git history.

Ownership and safety: current-user DPAPI registrations contain canonical paths and filesystem identities. Native handles pin ancestors and owned entries through writes/deletion; unknown files and same-name replacements are retained. Reports are outside the removable instance and exported before process termination. Exact recorded owner identity plus Windows Job containment stops the instance; an unrelated real-process sentinel survives. The reported process snapshot count is not a complete Job enumeration. Failed diagnostics prevent process stopping/deletion, and an exclusive entrance gate prevents concurrent startup/upgrade.

Managed data coverage includes transactional model credentials, persistent Pi sessions, SQLite database/journal, fixed rotating diagnostics, desktop lock/activation, installed code/dependencies and enrolled upgrade backups/version state. Node receives credentials and initial history through private pipes; model credentials remain in enrolled configuration and transaction files, while Pi’s credential store stays in memory. Diagnostics are bounded best-effort logs: busy writes can be dropped and interrupted rotation can duplicate entries. Unknown caches and uncommitted installation files are not inferred to belong to the instance.

Verified development evidence:

- Full suite at `40d7173`: **1,262 passed, 4 skipped** (325.27 seconds). Later install fix at `64e8c61`: **75 related tests passed**, plus both review axes without blockers; the full suite has not yet been rerun at that commit.
- Native regression checks cover junction/path replacement, altered receipts, unknown content, busy handles, diagnostics failure, credential write interruptions, SQLite commit interruptions, pipe EOF/blocked writes, process ownership and repeat cleanup. Real Pi localhost-provider sessions verify continuation; these do not establish external-supplier or visible GUI acceptance.
- Upgrade fault checks exercise code move failures, target-schema exceptions and process termination at backup/code/schema/version boundaries. Independent recovery preserves original version/code identity, credentials and tasks; failed recovery keeps startup blocked. Partial snapshots are never restored over data. Target initialization validates the current compatible pure-SQL/stdlib schema interface in the tool runtime; it is not arbitrary-code isolation or proof of every target runtime/dependency combination.
- The independently bundled tool completed minimal compatible-package install → task → upgrade → schema process exit → recovery → retained report → confirmed cleanup. Development zip `install/cleanup-tool-dev-public-2026-11-11-reviewed.zip` has SHA-256 `28f804f2cc14d64b676c59fcdfa80b347958c7d2c8ddc5947d14b41bd1e15f19`; tool-manifest SHA-256 is `1d6c71409fa2a8f5b0b6becf0734fe076eacf86b614d0c17e9ae904a8b38ca56`. Manifests support verification, not runtime tamper resistance.
- Full distribution installation initially failed at a deep SDK staging path. The public managed-install regression now passes with extended IO paths and unchanged canonical registrations. A fresh instance installed **17,465 files / 732 MB** in **125.66 seconds** using the independent bundled tool. The earlier incomplete instance remains retained. Packaged Python/Node/Pi then completed a localhost model turn; full-package upgrade exposed a separate old-code long-path check failure and safely restored the original state. The subsequent deep-code IO fix passed 66 related tests and both review axes; full-chain rerun remains pending. One initial hard-exit test returned after controlled rollback before its injection point; eight isolated fault cases and the 66-test rerun passed, but the original cause remains unknown. The test now prints only sanitized status/reason/recovery on unexpected return.

Supported Python launch entries now pass `-B`. With pinned runtime inputs and the complete package enabled, 44 related tests passed (110.92 seconds), including seven real no-new-bytecode boundaries, and TypeScript build/type checks passed. Coverage includes the tool/package/console wrappers, Node configuration/guardian/business children, generated shortcut target arguments, independent collector and package smoke. This also disables bytecode caching for ordinary supported launches. Existing unknown caches remain retained; arbitrary direct interpreter commands and source-toolchain repair commands are outside this guarantee. Shortcut target execution does not establish Shell double-click or visible GUI acceptance. Both review axes found no blockers.

Full-package upgrade remains incomplete: the pre-batching tool staging 17,468 files requires at least 87,340 full registration writes. A single in-memory sample measured 7.07 MB JSON and about 405 ms encryption; compression reduced the payload to 1.26 MB, but no full-run speedup has been established. A proposed exact-process interruption/recovery test was rejected by automatic approval review and remains pending explicit authorization; the running isolated transaction has not been terminated.

Bounded forward upgrade batches now register at most 32 empty files before writing payloads, and persist same-direction rename aliases before moving files. A 65-extra-file public upgrade fixture reduced registration writes from 435 to 55, bytes from 22,859,823 to 2,364,095, and measured time from 5.628 to 1.778 seconds; full-package performance remains unverified. Real exit tests cover old/new moves at positions 1/16/32, replacements, recovery interrupted again and receipt failure. Both review axes found no blockers. Two related runs each reported 77 passed / 1 failed: first, managed schema-exit recovery initially blocked then recovered on explicit retry with unknown cause; second, all 66 managed checks passed but an unchanged ordinary rollback rename raised WinError 5. That ordinary case passed once in a targeted rerun. Neither whole run is recorded as fully green. Diagnostic assertions now expose only fixed exception categories/error codes. Pre-receipt process death can leave up to 32 unknown empty files; they remain preserved, and directory registration/rollback are still individual operations.

New managed first installations use protocol 1: native-created directories/files receive persistent identity receipts before content writes, and pending installation blocks startup after lease acquisition. Real partial-write exits clean enrolled contents; replacements and pre-receipt empty objects remain preserved with partial status. Legacy two-field journals remain unchanged and are not adopted. Initial/directory/file/final receipt failures, invalid package zero writes and clean/reinitialize/reinstall checks pass. The related suite passed **203 tests, 1 skipped** in 305.91 seconds with the pinned standalone runtime enabled, including its install/upgrade/schema-exit/recovery/report/cleanup chain. The skip was the separately opt-in complete-package smoke test; two final native interruption cases passed after removing an unused test hook. Both review axes found no blockers. This new installation protocol still requires full-package performance validation; the earlier 125.66-second installation timing does not apply. The previously unexplained failures remain historical evidence despite this passing related run.

Installation now also batches at most 32 empty-file receipts before content writes, leaving directory receipts individual. Twenty interruption/failure checks passed, followed by 34 related tests / 1 opt-in full-package-smoke skip with the real standalone tool chain enabled. In the 65-extra-file fixture, receipt writes fell from 88 to 13 and bytes from 502,324 to 68,013; measured time increased from 1.085 to 2.230 seconds, so this is not a demonstrated speedup. Cross-batch partial writes and replacement preservation pass; pre-receipt exits can leave up to 32 unknown empty files, retained with partial status.

The independent tool now creates a fixed instance shortcut from in-memory native ShellLink bytes, then enrolls its FileID and digest before writing. Explicit managed-home startup rejects mismatched bindings and poisoned inherited home. Native interruption/replacement checks, upgrade retention, both cleanup modes and full-cleanup/reinstall pass; the related suite passed **47 tests, 1 skipped** (47.57 seconds), with pinned standalone runtime tests enabled. The skip was the optional complete-package bytecode smoke input. Both review axes found no blockers. One earlier fixture installation failed at registration replace with WinError 5 before COM execution; its cause remains unknown. Native link fields and controlled startup actors are evidence, not Shell double-click or visible GUI acceptance. The keep-data app-container gap was subsequently fixed: after complete package validation, public install can exclusively recreate an absent registered app while preserving data/archive identities and contents; replacements, junctions and pending upgrades are refused. The real bundled CLI performs keep-data cleanup → install → shortcut without manual initialization. The related suite passed 58 tests / 1 opt-in full-package-smoke skip in 69.67 seconds, and both review axes found no code blockers. Pre-receipt death leaves an unknown app container that blocks reuse rather than being adopted; full-package lifecycle acceptance remains pending.

Cleanup reports now add per-item deleted/already-absent/preserved/failed outcomes, process evidence, final content verification and a conservative next action. Unknown top-level names are hashed, including enumeration-failure paths; reports do not recursively claim unknown contents. Verification and cleanup.json publication remain under the entrance gate/exclusive lease, and retained containers must exist with original identities while their native handles stay held through report writing. Missing/already-exited owner evidence does not authorize a next-round readiness claim. The related run passed **227 tests, 1 skipped** (342.36 seconds), with real standalone runtime enabled; that process loaded the helper before the final retained-container fix. The final missing/dangling/replacement and handle-lifetime fixes separately passed **11 targeted checks**. Both review axes cleared the final source. Full latest-helper suite coverage is not claimed by combining those results.

The standalone CLI now exposes upgrade by validated instance ID, package and digest, resolving code/home internally. Its precheck refuses invalid artifacts before service diagnostics; the service still rechecks, and pending operations require explicit recovery. Twelve related tests passed / one opt-in full-package-smoke skip (29.26 seconds), including the actual bundled runtime upgrade/schema-exit/pending-refusal/recovery chain and invalid-input zero-write checks. Both review axes found no blockers.

A source-frozen full run at `d4df9a1` reported **1,333 passed, 3 skipped, 2 failed** (566.15 seconds): an outdated migration launcher expectation omitted `-B`, and a registration replacement raised WinError 5. The migration expectation was corrected (14 tests passed). Native experiments reproduced replacement contention with an open reader, but the original failing handle holder remains unknown. Registration-only publication now uses a native-created/flushed source handle and FileRenameInfoEx replacement with shared snapshot reads; ordinary content rename semantics are unchanged. Tests on Windows build 19045/NTFS verify old/new complete snapshots, publication-boundary process exits and refusal of external non-delete-sharing readers, read-only targets and unsupported operations. No retry, readonly override or power-loss guarantee is introduced; Windows 11 live remains pending. The related group reported **252 passed, 1 skipped, 1 failed** (353.30 seconds): a legacy model-flush injection hit the newly native registration write too early. Restoring that injection to its original body boundary passed all **3 targeted stages**. Both final review axes cleared the change. The fresh frozen run at `8906e39` passed **1,344 tests, 3 skipped** in 606.17 seconds with pinned standalone-runtime inputs and the complete-package bytecode smoke fixture enabled. Skips were optional legacy WeasyPrint rendering, an unavailable sample PDF, and a POSIX-only process-group test. Warnings were the existing PyMuPDF API deprecation and pytest cache write permissions. This passing run does not identify the earlier unknown handle holder or establish full-package lifecycle/GUI acceptance.

Managed Agent write_file outputs under data/outbox now use a fixed Python pipe bridge and native creation receipts. Matching owned files may be updated in place; interrupted updates can leave partial content, as before, and are not claimed atomic. Unknown objects, replacements, hardlinks and junctions are refused; keep-data retains outputs, full cleanup removes matching identities, and variable names/content are excluded from reports. Real local Pi write/read and standalone-runtime artifact → upgrade → hard-exit/recovery → cleanup checks pass. The related suite passed **240 tests, 1 skipped** in 429.81 seconds with pinned runtime inputs enabled; the skip was the separately supplied full-package bytecode smoke. Node build/type checks and both review axes passed. This covers the existing write_file tool, not other business writers or archive copies.

Existing managed local-material imports now enroll copies and the archive-index ledger. Copies publish only after content verification; ledger updates use identity-checked old/new recovery journals. Business indexing still does not confer cleanup ownership, upload callbacks retain their behavior outside the inventory lock, and external originals remain read-only. Reserved staging names are excluded from business indexing and refused as formal import names. Native interruption/replacement/retry tests and the actual standalone-runtime import/upgrade/recovery/cleanup chain pass. Both review axes cleared the final source. The related run reported **277 passed, 1 skipped, 1 failed** in 363.25 seconds; the failure was a collector fixture directory rename with WinError 5 before collector execution. Its targeted rerun passed once (2.09 seconds), without explaining the original holder. The skip was optional complete-package bytecode smoke. Other archive writers and upload side effects remain outside this slice; unknown or pre-receipt residues remain preserved.

Forward upgrade now batches directory receipts before publishing file contents or moving files. On Windows with real DPAPI and NTFS, the public 65-directory upgrade fixture reduced protected registration publications from 185 to 34 and total published bytes from 12,203,125 to 2,354,494. The single fixture timings were 3.219 and 3.584 seconds respectively. The focused native upgrade and recovery group passed 36 tests. An exit before receipt publication can leave up to 32 unregistered directory entries without file payloads, which cleanup preserves as unknown. Rollback directory receipts still publish individually.

On 2026-11-23, the independent tool built from the forward directory batching change tested preserved full P5 packages on Windows 10 build 19045, x64/NTFS. The fresh isolated install of 0.1.0-p5.3 took 235.54 seconds and enrolled a 17,469-file, 681,864,340-byte payload. Upgrade without a database correctly returned schema_database_snapshot_unavailable. After initializing a test task through AgentService, the tool upgraded to 0.1.0-p5.4 in 1,999.23 seconds, preserved the task and SQLite FileID, and cleared the pending marker. The independent report collected successfully. The inputs were two versions built from the same historical f895342 commit. This establishes successful full-volume code publication and schema initialization, with a 33-minute upgrade duration. Latest-source package verification, scale acceptance, and full-volume cleanup followed by reinstall remain pending.

Forward rename batches now retire their previous source aliases in the next required receipt, or in the final commit, while keeping the durable pre-rename receipt through each move. In a new 65-directory fixture comparison, registration publications dropped from 37 to 28 and total published bytes from 2,408,313 to 1,830,628. Single-run times were 2.345 and 2.495 seconds respectively. The 36-test native upgrade group passed. Two additional native process-exit cases immediately after the first rename of the next old or new batch also passed, restoring the original file identities. Full-volume timing with this change remains pending.

A five-sample native profile of the 16,556,165-byte full-volume protected snapshot measured median 36.2 ms for JSON encoding, 596.1 ms for DPAPI protection, 57.4 ms for envelope encoding and 24.5 ms for durable publication. Forward and reverse code moves now group up to 256 already-enrolled identities in one direction, while creation keeps its 32-entry bounds. A 320-directory upgrade reduced publications from 71 to 50 and bytes from 20,548,639 to 13,536,038. Single-run times were 9.165 and 9.911 seconds respectively. Forward batching alone exposed the existing 15-second recovery test guard for new-direction hard exits at moves 256 and 257. With reverse batching, all 43 focused native checks passed, including those exits, original FileID restoration, replacement refusal and a second recovery interruption.

The 2026-11-23 isolated Windows 10 build 19045 x64/NTFS run rebuilt the desktop renderer and Pi from source using matching retained locks and packaged Windows runtime inputs. The baseline and target used commits 679d06b and ba1c20e and each contained 21,197 payload files. The ZIP SHA-256 digests were `ee6eea74dd73b60c13886fee85c84b5a987b23be45417ca5b2fd51b4e897859a` and `d965aea68a6bc87dffbbecd884a9b3fb21462e25fcac2efd7eef551d30b05492`. Fresh baseline install took 291.121 seconds. A native tool process exited with code 77 after data-snapshot enrollment and before code staging. The pending marker refused a repeated upgrade; explicit recovery restored the baseline and preserved the SQLite FileID and seeded task. Recovery took 7.428 seconds. The target upgrade then succeeded in 1,073.157 seconds, task and FileID readback passed, and the independent report collected in 4.966 seconds. A read-only observer saw old-file moves start around minute 10 and new publication around minute 15, at approximately 25-second resolution. Independent cleanup exported its report but hit its 180-second guard. Readback found 42,379 of 42,400 enrolled application paths still present. There was no final cleanup record; reinstall was not started. Native recovery interrupted again after reverse moves 256 and 257 also passed in the separate fixture suite. The full-size injected interruption covered the pre-publication phase.

On 2026-11-24, the independent cleanup tool from 2141424 resumed that same partially cleaned instance. Its tool-manifest SHA-256 was `098206bc088c95f24fedcfec8e3dfcfc7a1bf08d67a74ec6486e1e2420767c1e`. Under the gate and exclusive lease it held the validated DPAPI receipt read-only and rechecked each deletion target and pinned registered ancestor. The tool finished in 230.109 seconds against an eight-minute guard. It reported 48,386 deleted entries, 21 already absent, zero residuals and a verified final cleanup report. The original report copies remained. This retry exceeds the first tool's three-minute guard, so the two results describe different tools, limits and starting progress. The tool reinitialized the same now-empty registration and reinstalled the pinned ba1c20e target in 264.056 seconds. Both the independent tool and the reinstalled package's Python verified a fresh database with no prior tasks or model configuration. The package's Node imported Pi 1.5.0. The cleanup report truthfully records unknown process evidence because no desktop owner was running in that round. A separate startup of the reinstalled desktop through the normal Python entry registered the real owner and spawned an Electron descendant. The production exact-owner stop primitive verified both observed processes exited in 33 ms. A combined full-package CLI cleanup test then attempted to start that same packaged desktop. The first attempt registered the owner but Electron exited with 0x80000003 before the bridge connected, triggering the normal startup timeout. No deletion occurred on that attempt. An unchanged retry detected the registered owner with a live Electron descendant and ran independent cleanup. In 56.545 seconds the tool collected the diagnostic report, confirmed both Job processes exited in 33 ms, preserved an unrelated Python sentinel and verified zero controlled residuals. The final report marked the instance ready to reinstall. These are CLI and process observations. Visible desktop behavior remains pending, and the intermittent Electron startup failure remains recorded. The final Windows cleanup, protected-registration and independent-tool regression with the full-package bytecode check enabled passed 230 tests in 402.55 seconds.

Read-only check of the full-volume packaged run found a newly created digest-named Electron binary cache outside the managed instance. It retained 73 files and 374,135,075 bytes after verified instance cleanup. Managed installed launches now verify and run their bundled Electron files in place without a second binary cache. The Windows native group passed 15 tests, including normal bridge routing, pinned target mutation, a second registered instance, legacy shared cache preservation and cleanup. The follow-up native group passed 21 tests, including rejection of implicit managed homes with another instance or developer checkout and acceptance of equivalent extended-length path spellings. The diagnostic and renderer process checks used a package installed into the matching managed instance. A separate 85-file registered fixture with actual Electron binaries launched successfully with the native runtime pins held from a default-like C volume path and cleaned with zero controlled residue. The fixture SHA-256 was `b4fc2b3af78cfe9aaf916f25080f6abd18ed591ba17fb7daa161746dd1075929`. Both direct and copied Electron failed pre-handshake on the E volume probe and authenticated on the C volume probe. A separate managed fixture with a 248-character executable path exited early with and without pins. The cause of that long-path failure was not determined.

Mutable profile ownership remains unresolved. An actual Electron BrowserWindow with a non-persistent session reported that session as non-persistent and still generated 34 files in its isolated userData/sessionData after a clean exit. Those files were not enrolled as immutable application files. The production managed launch does not assign a protected per-instance profile, and no existing shared or roaming profile was adopted or cleaned. An approved mutable-profile boundary and failure-safe creation evidence are required before including those files in cleanup authority.

Outstanding acceptance: end-to-end scaling, Windows 11 and visible desktop testing, repeated desktop startup reliability, mutable Electron profile ownership and complete interruption cleanup across pre-receipt unknown gaps. The current scope omits a Windows test kit. Snapshot/cleanup and installation have long-path regressions, but this is not a claim that every runtime path supports arbitrary length. No final Windows/Mac kit or new Notion manual has been delivered, and no release tag was pushed.

## Spec 1 Agent acceptance — 2026-11-16

A1–A3 are closed with linked evidence in [Spec 1](https://github.com/Moke110/horizon/issues/181). The user observed tray restoration, durable exit/pause, Continue/Abandon, configuration/authentication/quota/transient errors, schedule create/edit/pause/restart, and matching consecutive run results without selection drift. Controlled delivery observations covered success, unknown lookup, explicit risk authorization and resend. No external recipient was contacted; production delivery remains unconfigured by default.

Frozen runtime commit `463b5d0` passed **1,410 tests, 3 skipped** in 515.47 seconds with pinned standalone-runtime and complete-package bytecode inputs. The existing fitz deprecation was the only warning. The final native cleanup/process group passed **202 tests**, including cross-process admission, exclusive-gate interoperability, pending-upgrade checks and fault injection. Both review axes cleared the changes. Recovery now retains failed-session context correctly; missing configuration remains visible; task selection follows identity. Runtime admission uses shared native gates, cleanup/upgrade retain exclusive gates, and the stable native OVERLAPPED type supports nested bindings.

A4 candidate `0.1.0-a4.3`, built from `463b5d0`, reproduced twice with SHA-256 `719da9f7b7d1c66baae1d2063da436fab372230e27fd8627c8c5bc8f0960fdc0` (292,882,232 bytes); all 17,769 manifest files were verified. Pinned components are Python 3.13.7, Node 24.16.0, Electron 45.2.3 and Pi 1.5.0. On Windows 10 build 19045 x64, the fresh managed installation completed in 214.01 seconds. Actual installed-runtime checks under minimal PATH passed: main session plus two distinct Pi children and tool results, 401/manual Continue, two concurrent schedules and offline cursor coalescing (234.61 seconds), separate-process restart retaining original context/tools/children (38.84 seconds), and pause/continue/cancel/late-result rejection (97.60 seconds). No Feishu configuration was required. Installed-runtime native probes verified zero console, normal/forced descendant cleanup and external-sentinel survival.

The same artifact passed public ordinary installation and verification under a fresh Windows local-application directory (59.79 seconds). Its bundled Python 3.13.7 launched the packaged Electron UI under minimal PATH; the user confirmed the acceptance task/message was visible without an extra console, then explicitly exited. The recorded owner and all four Electron processes were absent afterward. Development-interpreter control windows were excluded and stopped. Startup probes varying STARTUPINFO, handles and creation flags did not resolve the workspace-hosted failure; identical Python/helper bytes ran successfully from C instead of the E workspace. This isolates an execution-context difference without identifying the OS mechanism. No product workaround, runtime-pin change or sandbox disabling was applied; C-hosted helper preparation also passed.

[A4 evidence](https://github.com/Moke110/horizon/issues/191) preserves earlier failed candidates, the unsafe port-10080 fixture failure and corrected high-port tests, and native binding regressions. The additional full-package upgrade attempt failed at its 600-second guard during staging before original code moved. Supported recovery succeeded in 288.45 seconds and retained original database identity, sessions and child associations with no pending operation. This is not a successful latest-package upgrade or a performance acceptance claim; staging scalability remains under [P5 / Spec 6](https://github.com/Moke110/horizon/issues/230). A4 reuses the previously completed #129/#130/#132 real-provider/session evidence and #131 Windows 10 installation/upgrade/rollback/uninstall evidence. Windows 11, real Feishu integration and final platform kits remain outside this acceptance. No release or tag was published.


## Voyager V1/V7 acceptance — 2026-11-16

[V1 #192](https://github.com/Moke110/horizon/issues/192) and [V7 #198](https://github.com/Moke110/horizon/issues/198) passed development-machine acceptance at frozen implementation `9b0a0c0`. The full suite passed **1,449 tests, 5 skipped** in 475.43 seconds (one existing fitz deprecation warning); related checks passed **56 tests, 2 skipped**, and frontend typecheck/build and both final review axes passed. Two native symlink cases lack host privilege; hardlink and directory-junction refusal were verified.

Candidate `0.1.0-voyager.1` reproduced twice with SHA-256 `0f2f6c8050103e9f8ab616ec64ca6ae1d362e4593801af69f403480922ebad8a` (292,905,319 bytes, 17,775 manifest files). Windows 10 build 19045 x64 used Python 3.13.7, Node 24.16.0, Electron 45.2.3 and Pi 1.5.0. Public C-hosted installation and full verification passed in 62.47 seconds, retaining the A4 isolated data home and original task. Actual bundled services completed four controlled localhost queries with budget stops and explicit continuation, deduplication and replay without extra calls. Another bundled-interpreter process read back the same results and offline original. Actual bundled Pi attempted write_file against an independently registered snapshot; the tool refused and its original remained unchanged. These are controlled local proofs, not external-source acceptance.

The user registered text through the installed GUI and confirmed literal original text, source/time/hash/extraction metadata and a separate absent-summary section. The user also submitted a bilingual explicit scope with must/exclude/source constraints and default budgets; the GUI correctly showed unavailable sources and gaps. Post-exit public-service readback retained exact original/hash and constraints, four unconfigured query plans, four gaps and zero source calls; no success cursor was fabricated. The user confirmed no extra console and explicit Exit; recorded owner/descendants and the unique acceptance invocation were absent afterward.

External connectors/live source evidence remain with V2–V5; the rest of [Spec 2](https://github.com/Moke110/horizon/issues/182) remains open. No release or tag was published.

## Voyager academic discovery — 2026-11-20

At `50cf2c2`, the desktop command probe searched arXiv and Europe PMC for photometry in the 2020 UTC publication window. Each source returned two distinct candidates over separate execution segments. The probe verified the publication bounds, recorded source requests and identical results after reopening the service. The arXiv IDs were 2012.15177v1 and 2012.15166v1; the Europe PMC IDs were MED:33385438 and MED:33068635. The first frozen run reported an arXiv source failure; the repeat passed both sources. The development machine ran Windows 10 build 19045 and Python 3.13.13. The relevant service and source suite passed 36 tests. Desktop typecheck and build passed. Candidate 0.1.0-voyager.2 from 9724ba7 (SHA-256 116091d98c19ec3e50561bed6b9d026e8efb0db24cc0203df734beb80402760d) passed full installed verification. The Explorer confirmed both groups matched in the GUI, no extra console appeared and they selected Exit. The recorded pythonw owner and all four Electron processes were absent afterward. See [V2](https://github.com/Moke110/horizon/issues/193).

## Voyager subscriptions and PDF reader — 2026-11-20

Candidate 0.1.0-voyager.3 from c907cbf passed installed verification (SHA-256 a7728fb5c8b8dddf64fb43d44cfd2cea27f013e79b92c8fba9bdf00517ebb518). The installed runtime observed RSS 27 → 0, Atom 15 → 0 and a site section 290 → 0 over two refreshes. It also verified the original three-page PDF hash and page-specific extracted text. The Explorer confirmed both the subscription display and the three-page reader check were correct, with no extra console. The recorded pythonw owner and four Electron processes were absent after the check. [Subscription evidence](https://github.com/Moke110/horizon/issues/196#issuecomment-7041659067) and [PDF evidence](https://github.com/Moke110/horizon/issues/199#issuecomment-7041659691).

## Voyager V9 webpage acceptance — 2026-11-20

At frozen source `7ba07a7`, Windows 10 x64 and CPython 3.13.13 ran
`python -m scripts.probe_voyager_web` with Trafilatura 2.3.1. The public
Materials service captured four live pages, replayed each operation, reopened
its saved material, verified the text digest and resolved every paragraph
anchor. NASA's Hubble news article yielded 14 paragraphs (5,773 bytes),
the Python Insider release blog 35 (3,352), IANA's Example Domains page
10 (739), and Python's bisect documentation 150 (8,369). All 15 Windows
CPython 3.13 release-wheel versions matched their downloaded metadata.

The Windows x64 local candidate `0.1.0-voyager.5` from `bf7b87f` installed
in a fresh C-hosted directory and verified all 20,597 manifest files. The ZIP
SHA-256 was `b88f7ff59f094d771ded1ad3d765224210f228972101ede8f72d5f53d3a9df82`.
Bundled Python 3.13.7 used Playwright 1.65.2 and Chromium 159.0.8154.6
(revision 1255). The Chromium executable's SHA-256 was
`9b5b0b9d91b08bc432f53ddf9946aaec7ab100b3f887819656c2ee63cde1757e`.
At the frozen commit, the full Python suite passed 1,485 tests with 15 skipped.
Desktop typecheck and build passed.

The installed Materials service captured 10 paragraphs from the public Quotes
to Scrape JavaScript page, with Albert Einstein in anchors `p1`, `p3`, and
`p6`. The initial response lacked the marker in visible text. Same-operation
replay used the stored result, and a new service instance verified the snapshot
and reopened the same anchors and text digest. A live 404 preserved the original
link and reason with empty original text. Earlier attempts reported capture
failure and are retained. The previous candidate timed out at serial resource
fetching. The passing probe observes the actual initial response without sending
an extra preflight request.

Native installed-runtime probes observed Chromium, Playwright Node, Python, and
console-host descendants. Cancellation and a navigation timeout both left no
observed descendants or per-capture temporary directories. The probes supplied
invalid external driver and browser locations and still observed the bundled
browser. On November 21, the Explorer confirmed the installed desktop showed
numbered paragraphs containing Albert Einstein, with no extra console during
capture, and explicitly exited from the tray. Installed-service readback after
exit verified the same 10 paragraphs, anchor IDs, and text digest. The recorded
Python owner and four Electron processes were gone, and no matching candidate
process remained. See the [V9 ticket](https://github.com/Moke110/horizon/issues/200).

## Voyager DOI and source settings — 2026-11-21

Windows x64 candidate `0.1.0-voyager.7` from `0d4c4dd` installed and
verified all 20,598 files. The ZIP SHA-256 was
`b194fa90871e853e2237b3ec0257742b547b0fd03fb5d5a9cb36c8c0fbb81f2f`.
Desktop typecheck and build passed. The related Python suite passed 284 tests.
The installed command confirmed the Tavily allowance persisted after reopening,
cleared on key rotation, and returned only the setting state and limit.

The original candidate received two OpenAlex pages and reopened both. It also
rejected a later page containing pre-1970 publications. The revised installed
runtime retained six publications dated before 1970 in a discovery-time window,
and fetched and reopened two distinct Crossref pages with score ordering. Both
sources retained DOI `10.1016/j.nuclphysa.2016.02.011`. OpenAlex listed 45
references and reported 45. Crossref listed 40 and reported 45. See
[V3](https://github.com/Moke110/horizon/issues/194).

Tavily authenticated and reported free Researcher plan credits and zero PAYG
usage. Its usage response returned null for both the key and PAYG caps. The
Explorer verified their Tavily dashboard settings, then saved the PAYG
confirmation and monthly limit in the new candidate. Its installed runtime
ran one Search basic request in 2.95 seconds, recorded one credit and its
receipt, and reopened five metadata-only results. The candidate cap stopped
the retrieval segment with the partial result retained. See
[V4](https://github.com/Moke110/horizon/issues/195).

## Voyager selection and full text — 2026-11-21

Windows x64 candidate `0.1.0-voyager.8` from `4208ba0` installed and
verified all 20,600 files. ZIP SHA-256
`1876f151805bc11e230dda93ac8e38c072d785d110afd45bfb6b2cd0ae37f33c`.
Desktop typecheck and build passed. The related Python suite ran 100 passing
tests with two skips. In the installed runtime, a real Crossref request
retained eight new candidates with scope terms and source receipts, preserved
the remaining search gap at the query limit, and reopened the same view.
Repeated discovery, scope linkage and repost treatment are covered by
controlled tests. See [V6](https://github.com/Moke110/horizon/issues/197).

Using controlled discovery metadata and real network bodies, the installed
full-text service captured an arXiv PDF, NCBI JATS XML and PLOS HTML. It
reopened each snapshot and retained the XML and HTML as separate versions of
the same DOI. A real HTTP 404 reopened as awaiting full text with no original
body. A source-environment run also recovered a usable PDF from an OpenAlex
open location after a publisher XML redirect failed. The installed Unpaywall
settings passed save, reopen and removal without returning the contact.
After the Explorer saved their contact and explicitly authorized one query,
the installed Unpaywall locator returned HTTP 200 and two open-location
links for DOI `10.1371/journal.pcbi.1003285`. The contact was absent from
the locator receipt and diagnostic output. See
[V10](https://github.com/Moke110/horizon/issues/201).

## Voyager waiting materials and archives — 2026-11-21

Windows x64 candidate `0.1.0-voyager.9` from `a45f0b2` installed and
verified all 20,604 files. ZIP SHA-256
`4d9d85e8f9edb2017cf6b383f6176d0765499704a0444bc4c6f6527130b02876`.
Desktop typecheck and build passed. The related Python suite passed 112
tests with two skips.

With isolated test materials, the installed runtime bound abstract consent
to the original task, extracted a supplied PDF as a new readable version,
preserved the earlier record, and reopened an abandoned material. The
schedule regression tests exercised paused plans, existing work and a
coverage-neutral late-full-text turn. See
[V11](https://github.com/Moke110/horizon/issues/202).

The installed runtime reopened an archived snapshot and its registered
citation, rejected a mismatched deletion confirmation and preserved a removed
citation marker. It retained text shared by a second material and collected
the blob only after its final owner was removed. The reference tests use
file-backed snapshots and precede Editor integration. See
[V12](https://github.com/Moke110/horizon/issues/203).

## Voyager five-channel package verification — 2026-11-21

Windows x64 candidate `0.1.0-voyager.11` from `4659167ee7ece6c7c6e24e0057441803108f81f8`
was built twice with matching ZIP and manifest hashes. The clean AppData
install verified all 21,175 files and the bundled Node loaded Pi 1.5.0.
ZIP SHA-256 `6ba542db2e46fbf211fe4aa77729c2eae4247ffca0c7953affc8cca2f7d8ff04` and
manifest SHA-256 `79cd91cbd953559c03fa91cc5faf8a25a2467e696c7de6c2b4adbe9d73a2f936`.
The inventory reported 258 entries and each referenced license notice was
present. Pi declares MIT and supplies no separate notice path. Two Windows Job
cleanup tests passed. The full Python run before the renderer adjustment
passed 1,514 tests with 15 skips. The targeted run with the renderer adjustment
passed 22 tests, including the role, web and process checks. Agent and desktop
builds passed, including desktop typecheck.

- Installed `.10` arXiv returned four dated, in-window Kepler-1625b candidates
  and a receipt. Reopening preserved the same response. The one-query budget
  persisted partial coverage and pending pagination. Installed `.9` captured
  results from Crossref, OpenAlex and Europe PMC, including a saved public
  OpenAlex PDF. See the earlier source, DOI and full-text entries for their
  relationships, fallbacks and limitations.
- Installed `.9` captured the IANA Example Domains URL as 739 text bytes with
  ten anchors and retained original HTML. Isolated user text reopened with
  all 36 bytes intact. Live RSS, Atom and section refreshes returned 27, 15
  and 290 entries respectively, then zero new entries for all three. A fresh
  service reopen retained the timestamps and results with no gaps.
- Installed `.11` captured the public JavaScript-rendered Quotes to Scrape
  page from the clean C-volume AppData install with a minimal PATH and
  intentionally invalid external Playwright overrides. It stored 1,121 text
  bytes, ten anchors and the rendered HTML hash. Reopen verified the
  snapshot, and same-operation replay returned the same result. A separate
  E-volume smoke install verified its files, but its Chromium driver failed
  at launch with `0x80000003`. The default AppData install completed the live
  capture.
- React markup fixtures exercised full-text supplement, abandonment and
  task-bound abstract consent, partial coverage, no-new status and Tavily
  budget status and controls. The 15 focused Tavily tests covered the
  free-budget stop and unknown allowance. These checks used rendered markup
  and service state.

- Installed `.11` (`C:/Users/Chang/AppData/Local/Horizon-V11-4659167e/code`)
  completed a real Pi-to-Voyager delegation with the saved model, exit code 0
  and no additional Unpaywall contact. Parent turn
  `02bd87c8-958e-4309-adc5-85f9b25541da` had exactly one successful Voyager
  child, `b99386e4-8c1c-4744-854c-f9c4677af9ed`, with an independent session,
  nonempty analysis and an exact match to the stored evidence projection,
  including its evidence limitations.
  The explicit temporary OpenAlex request `5cc5820f-5719-470e-987d-aba99cc402b5`
  returned four candidates and one coverage gap. Material
  `8dd57d99-8327-43f5-b2ce-16d561222558` had a verified snapshot ID and hash,
  four anchors and a selection reason.

The installed AppData candidate started interactively without a console. The
C-volume Python GUI owner (PID 68492) launched Electron (PID 70544), which
owned the sole Horizon application window. The Explorer selected
`Use delegate with role v` and opened `轨迹`. They confirmed that
exactly one Voyager child displayed `succeeded` and showed its analysis.

The first interactive tray exit, `退出 Horizon（暂停任务）`, recorded
`desktop_exit`. The Python owner (PID 68492), Electron main process (PID
70544) and all three tracked descendants were absent after exit. The verified
C-volume AppData install relaunched without a console using an isolated,
credential-free offline fixture with its own archive. The fixture made no
live provider requests.

In `204 Acceptance - Pending Materials`, the Explorer supplemented `Solar
Fixture - Supplement` v2 with a PDF. Extraction completed and stored snapshot
`98de6c79-38e3-463f-9dae-32387defa607`. The Explorer abandoned the separate
`Solar Fixture - Abandon` v2 and authorized `Solar Fixture - Abstract Consent`,
which persisted one consent for that task. A screenshot of `204 Acceptance -
Scope Control` showed the same material still marked `待全文`, with no
task-specific abstract authorization or interpretation controls.

The Explorer confirmed three retrieval details in the installed `.11` GUI.
The partial-coverage result displayed an OpenAlex source failure in its gaps.
`solar · 无新增` showed completion without a stop reason. The offline
`fixture Tavily hard stop · 执行失败` displayed the gap
`tavily · 来源额度未知`. The matching offline provider check called usage,
search, then usage, and blocked the second search before dispatch. The earlier
live `.7` check consumed one free credit. The hard-stop scenario used the
offline fixture. See [V13](https://github.com/Moke110/horizon/issues/204).

## K5 knowledge history and undo — 2026-11-22

The Windows x64 candidate `0.1.0-k5.1` built twice from `f446cbb` with ZIP
SHA-256 `7341e10ab5b36033494650f01da122087c5308c7f1022035392ac3876c024078`
and a matching 21,186-file manifest. Fresh installation and the minimal-PATH
wrapper passed. A real `0.1.0-voyager.11` → `0.1.0-k5.1` upgrade in the same
data home retained the earlier conversation, IANA source snapshot and Crossref
retrieval. The upgraded knowledge base was empty before the GUI walkthrough.

The Explorer confirmed the full GUI sequence. Two supplied screenshots show
the undo preview against v2 and the completed v3 with v1 and v2 still visible.
Database readback confirmed the Frontier and Theme links, the restored card
content and the unchanged prior conversation and retrieval. Explicit desktop
exit returned 0 with no remaining processes for the instance.

The installed service preserved unrelated changes, rejected a stale or
overlapping undo and replayed the original receipt. Live Crossref queries
changed from Kepler-1625b to TRAPPIST-1 after the Frontier edit, returning six
qualifying candidates each, with different receipts and unchanged prior
results. The workflow used no Feishu adapter. The candidate's Windows suite
passed 1,587 tests with 15 skips and two dependency warnings. [K5 acceptance evidence](https://github.com/Moke110/horizon/issues/209)

## K6 Keeper review and confirmation cards — 2026-11-22

The Windows x64 candidate `0.1.0-k6.1` built twice from `fe36ed0` with ZIP
SHA-256 `3c0649bdd9b100347b1c56c09ca4351d9dece73f68d9f891809fa8eeaa0fdfa5`
and a matching 21,190-file manifest. The real seven-stage installation and the
distribution wrapper passed. The installed runtime captured IANA Example
Domains paragraphs `p3` and `p6` and ran a bounded Keeper subprocess on the
same task as a completed, explicitly labeled pre-Editor test report.

Keeper split unrelated create, update and delete changes into three pending
cards and returned a separate request with missing evidence. Pin, role,
source and replay checks passed. Pending cards left committed knowledge
unchanged and a new plan obtained the only execution slot.

The Explorer confirmed the GUI sequence. Their screenshot shows the read-only
update diff, report version, task version and Keeper child association. SQLite
readback matched all three pending cards and confirmed no knowledge writes.
Explicit desktop exit returned 0 with no remaining processes for the instance.
The candidate's Windows suite passed 1,619 tests with 15 skips and two
dependency warnings. [K6 acceptance evidence](https://github.com/Moke110/horizon/issues/210)

## K7 atomic Keeper decisions — 2026-11-22

The Windows x64 candidate `0.1.0-k7.1` built twice from `98c9c5b` with ZIP
SHA-256 `b175b681f757647f4926334aa77a54817e410d41d7bc134ce4e18d0500f8c04d`
and a matching 21,192-file manifest. The seven-stage installation and the
distribution wrapper passed. Independent Windows clients returned the same
receipt when they submitted one operation concurrently. An injected failure
on the second write, a stale version and an unauthorized actor left knowledge
and audit unchanged. An approved group could not be applied again.

The installed runtime applied a mixed Card and Theme group atomically and
left rejected knowledge unchanged. A repeated rejected change was suppressed
with the same evidence even when an explanation was added. With new RFC 2606
evidence and an explanation, the changed request returned to pending.

The Explorer confirmed the GUI sequence, including restart. SQLite readback
found one approval that advanced both targets to v2 and one rejection that left
its target at v1. Each decision had one Explorer audit entry with the bound
proposal, revision, operation and target versions. Both desktop exits returned
0 with no remaining instance processes. The candidate's Windows suite passed
1,655 tests with 15 skips and two dependency warnings. [K7 acceptance evidence](https://github.com/Moke110/horizon/issues/211)

## K8 Keeper revisions after conflicts and feedback — 2026-11-22

The Windows x64 candidate `0.1.0-k8.2` built twice from `0cff027` with ZIP
SHA-256 `3870bf91dc22b295e50134160cd0f68bc3d90918e933cda95fb0dd38d9aa6ba7`
and a matching 21,193-file manifest. The seven-stage installation and
distribution wrapper passed. The first GUI run on `0.1.0-k8.1` returned both
proposals at v1 without writing knowledge. The reused embedded Pythonw searched
its own installation for the worker and exited with a module import error.
The corrected desktop launches the worker from the selected package and shows
a new revision only after one is published. Both attempts then succeeded when
retried through the installed bridge on a copy of the failed instance data.

The Explorer confirmed both GUI retest paths. SQLite readback showed that
the old approval wrote nothing, a real Keeper child reviewed against current
knowledge, and explicit approval of proposal v2 advanced the target to v3
while preserving the manual edit. A separate adjustment kept one of two
changes pending, preserved the feedback in the original task's Keeper child,
and wrote no knowledge. The desktop exited with code 0 and left no instance
processes. Installed checks with IANA source snapshots covered concurrent
edits, stale approvals, repeated operations and cancellation. The Windows
suite passed 1,671 tests with 15 skips and two dependency warnings. Both
renderer regression tests passed. [K8 acceptance evidence](https://github.com/Moke110/horizon/issues/212)

## K9 Keeper merges, opposing evidence and group undo — 2026-11-23

The Windows x64 candidate `0.1.0-k9.1` built twice from `d6df854` with ZIP
SHA-256 `b07ae675206ff0e71885120f37113ea4f30e96167d9c211d70de1e64676e80cf`
and a matching 21,196-file manifest. The seven-stage installation and
distribution wrapper passed. The installed runtime used saved WHATWG and W3C
snapshots to verify exact duplicate supplementation, preservation of opposing
citations, and an explicit two-card merge with inbound relation changes. A
similar-name Card stayed independent. A failure on the second undo write left
the group unchanged. A later manual edit blocked undo, and repeating the same
operation returned its existing receipt.

The Explorer confirmed the GUI sequence. Three screenshots showed the merge,
the opposing sources and the independent approval and undo receipts. SQLite
readback found all four owners at versions 1, 2 and 3, with the old approval
preserved. The approved Theme relation kept the reviewed survivor at v1. The
undo pinned the restored donor at v3. A separate readback and undo using a
copy of the earlier K8.2 accepted data restored a v3 record to v4 while keeping
the manual body and the original approval. Explicit desktop exit returned 0
with no remaining instance processes. The Windows suite passed 1,695 tests
with 15 skips and two dependency warnings. All 12 renderer tests passed.
[K9 acceptance evidence](https://github.com/Moke110/horizon/issues/213)

Spec 3 closes the local knowledge and Keeper stage. The Windows evidence
uses the installed desktop and explicitly marked pre-Editor requests attached
to completed Agent tasks. The local workbench and knowledge changes require
no Feishu account. Physical Mac testing and the Mac test suite and handbook
are tracked in the final delivery phase. [Spec 3 evidence](https://github.com/Moke110/horizon/issues/183)

## Closed-spec source audit (#239)

A fresh worktree of `617a2b4` on Windows on 2026-11-23 contained the three completed numbered specifications (#181–183). GitHub recorded the superseded #120–127 as closed without implementation. The following index connects the approved ticket criteria and the historical live evidence above to the tracked source entry points. Paths in the last column are relative to the checkout.

| Spec and tickets | Behavior | Source entry points |
|---|---|---|
| #181 / #188 | Tray, exit, recoverable failure | `desktop/src/main.ts`, `agent/src/main.ts`, `hostrun/desktop_worker.py` |
| #189 | Concurrent schedules, offline catch-up and coverage | `hostrun/agent_scheduling.py` |
| #190 | Delivery receipts and unknown-result recovery | `hostrun/agent_delivery.py` |
| #191 | Integrated Agent behavior | `hostrun/agent_service.py`, `agent/src/main.ts` |
| #182 / #192 | Scope, exclusions and query budget | `hostrun/voyager_service.py` |
| #193, #194 | arXiv/Europe PMC and DOI discovery | `hostrun/voyager_sources.py`, `hostrun/voyager_doi_sources.py` |
| #195, #196 | Web budget and incremental subscriptions | `hostrun/voyager_tavily.py`, `hostrun/voyager_subscriptions.py` |
| #197 | Relevance, counterevidence and coverage | `hostrun/voyager_relevance.py` |
| #198, #199 | Text snapshots, PDF extraction and reader | `hostrun/voyager_materials.py`, `hostrun/pdf_worker.py`, `desktop/src/PdfReader.tsx` |
| #200, #201 | Web snapshots and versioned full text | `hostrun/voyager_web.py`, `hostrun/voyager_web_renderer.py`, `hostrun/voyager_fulltext.py` |
| #202, #203 | Waiting for full text, archive and reclamation | `hostrun/voyager_pending.py`, `hostrun/voyager_late_fulltext.py`, `hostrun/voyager_material_lifecycle.py` |
| #204 | Integrated discovery and child evidence | `hostrun/voyager_service.py`, `hostrun/voyager_child.py` |
| #183 / #205 | Frontier creation and real scope provider | `hostrun/frontier.py`, `hostrun/voyager_service.py` |
| #206, #207 | Manual cards, conditions and opposing evidence | `hostrun/knowledge_cards.py`, `hostrun/knowledge_themes.py` |
| #208, #209 | Workbench search, version history and undo | `hostrun/knowledge_workbench.py`, `hostrun/knowledge_history.py` |
| #210, #211 | Sourced proposals and atomic decisions | `hostrun/keeper.py`, `hostrun/keeper_decisions.py` |
| #212, #213 | Conflict revisions, merge and group undo | `hostrun/keeper_revisions.py`, `hostrun/keeper_merge.py`, `hostrun/keeper_group_undo.py` |

Built Agent from its lockfile with `npm ci` and `npm run build` in `agent/`. The packaged-code entry point `npm run check` reported horizon-pi 1.5.0. Built desktop renderer/main from its lockfile with lifecycle scripts disabled for dependency installation. The clean checkout ran 12 Keeper renderer tests and the Python Agent, Frontier, Knowledge, Keeper and Voyager test modules (352 passed, two skipped). The Python environment supplied dependencies only. This run covered source behavior. It did not exercise provider credentials, interactive desktop visuals, installation or release state.

All local Spec 2/3 ticket branch tips were ancestors of this checkout. The eight modified tracked files and untracked Voyager child module in the old #204 worktree matched Git blobs in ancestor `9cb5052` individually. The old #205 worktree had no tracked changes or extra implementation files under the Agent, desktop or host runtime source directories. No code transplant was required for these worktrees.

### Acceptance checklist lookup

Numbers below are the one-based checkbox positions under each original ticket’s Acceptance Criteria. Each row follows those positions from left to right. Named panels and readers are tracked under `desktop/src/`, Python modules under `hostrun/`. “Evidence” refers to the dated section above and the original ticket’s acceptance comments. For #189–190 and #192–213, item 6 is the behavioral verification record in that ticket and its matching `tests/test_*.py` suite. Item 7 is the ticket’s delivery record, the changed module contract in `contracts/`, and `README.md` / `README.zh-CN.md`. Package-specific items reference historical verification only.

| Ticket | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| #188 | Tray and exit: main.ts, desktop_worker.py | Retry presentation: app.tsx and agent/src/main.ts | Developer-machine evidence | — | — |
| #189 | SchedulePanel.tsx | agent_capacity.py | agent_scheduling.py serialization | agent_scheduling.py catch-up | tests/test_agent_scheduling.py |
| #190 | agent_delivery.py enqueue | agent_delivery.py unknown outcome | DeliveryPanel.tsx recovery | agent_delivery.py cancellation | tests/test_agent_delivery.py |
| #191 | AgentService and desktop_worker.py | Historical #131, #129, #130, #132 evidence | Package and Windows evidence in #191 | — | — |
| #192 | voyager_service.py planning | voyager_service.py budgets | voyager_service.py cursor | VoyagerPanel.tsx | tests/test_voyager_service.py |
| #193 | voyager_sources.py connectors | voyager_sources.py candidate normalization | voyager_sources.py rate control | voyager_service.py partial result | #193 live evidence and tests/test_voyager_sources.py |
| #194 | voyager_doi_sources.py discovery | voyager_doi_sources.py relation metadata | voyager_doi_sources.py quota | voyager_service.py candidate merge | #194 evidence and tests/test_voyager_doi_sources.py |
| #195 | voyager_tavily.py search | voyager_tavily.py budget | voyager_tavily.py metadata | VoyagerSourceSettings.tsx | #195 evidence and tests/test_voyager_tavily.py |
| #196 | voyager_subscriptions.py feed discovery | voyager_subscriptions.py column discovery | voyager_subscriptions.py incremental state | SubscriptionPanel.tsx | #196 evidence and tests/test_voyager_subscriptions.py |
| #197 | voyager_relevance.py selection | voyager_relevance.py counterevidence | voyager_service.py material identity | voyager_service.py empty selection | voyager_service.py partial coverage |
| #198 | voyager_materials.py import | voyager_materials.py snapshot | voyager_materials.py idempotency | MaterialsPanel.tsx | voyager_material_lifecycle.py |
| #199 | pdf_worker.py and PdfReader.tsx | tests/test_voyager_pdf.py page cases | pdf_worker.py extraction status | Historical package dependency evidence in #199 | #199 packaged PDF evidence |
| #200 | voyager_web.py static fetch | voyager_web_renderer.py rendering | voyager_materials.py snapshots | voyager_web.py restrictions | voyager_web_renderer.py child cleanup |
| #201 | voyager_fulltext.py retrieval | voyager_fulltext.py content checks | voyager_materials.py identity | voyager_service.py selection | #201 evidence and tests/test_voyager_fulltext.py |
| #202 | PendingMaterialActions.tsx | voyager_pending.py authorization | voyager_pending.py abandon | voyager_late_fulltext.py enqueue | voyager_late_fulltext.py task state |
| #203 | voyager_material_lifecycle.py archive | voyager_material_lifecycle.py reference policy | voyager_material_lifecycle.py reclaim | tests/test_voyager_archive.py | MaterialsPanel.tsx |
| #204 | VoyagerPanel.tsx and #204 source traces | MaterialsPanel.tsx and #204 material traces | desktop_worker.py and voyager_child.py | Historical independent-install evidence in #204 | VoyagerPanel.tsx scope |
| #205 | KnowledgePanel.tsx | frontier.py scope provider | tests/test_frontier.py versioned queries | frontier.py status transitions | KnowledgePanel.tsx edit |
| #206 | knowledge_cards.py versions | knowledge_cards.py source status | knowledge_cards.py confirmed knowledge | knowledge_cards.py edit guards | KnowledgeCardPanel.tsx |
| #207 | knowledge_themes.py conditions | knowledge_themes.py evidence direction | ThemePanel.tsx source opening | knowledge_themes.py reference validation | tests/test_knowledge_themes.py |
| #208 | knowledge_workbench.py normalized search | knowledge_workbench.py filters | knowledge_workbench.py state filters | KnowledgeWorkbench.tsx | #208 desktop evidence |
| #209 | knowledge_history.py audit | knowledge_history.py undo | KnowledgeWorkbench.tsx and #209 evidence | Historical upgrade evidence in #209 | #209 independent-home evidence |
| #210 | keeper.py request | keeper_worker.py | KeeperProposalPanel.tsx | tests/test_keeper.py request boundary | keeper.py pending state |
| #211 | keeper_decisions.py version binding | keeper_decisions.py transaction | keeper_decisions.py rejection | keeper_decisions.py suppression | tests/test_keeper_decisions.py |
| #212 | keeper_revisions.py stale version | KeeperProposalPanel.tsx | keeper_revisions.py adjustment | keeper_decisions.py decision guards | tests/test_keeper_revisions.py |
| #213 | keeper_merge.py | keeper_evidence_policy.py | keeper_group_undo.py | tests/test_keeper_group_undo.py | KeeperGroupUndoPanel.tsx and #213 desktop evidence |

## Development scratch relocation (#240)

On 2026-11-23 the maintainer checkout retained old test runs and staging archives in `tmp/legacy-240/` under their original basenames. Of 766 selected root entries, 493 moved completely and 273 old test directories encountered Windows access denials. Each of those 273 has both a root path and a destination; inspect both before further cleanup. Eight additional historical build/reproduction directories moved by atomic rename. Seventeen other pytest/process-test directories refused rename. No ownership, ACL or junction changes were made. Only the reproducible root CPython cache file for `common.py` was deleted.

The build-only dependency directories `cleanup-tool-inputs`, `v9-static-wheels` and `v9-browser-wheels` now live under `tmp/legacy-240/install/`. Historical path mentions in acceptance evidence describe where the original run occurred. Cleanup-tool assembly now stages its wheels in a unique directory under the checkout's `tmp/`. The General Software Development Workflow in Notion records this rule for relocatable development material; the saved rule was read back.

Before any further removal, review the remaining root test paths and `.pytest_cache/` with their Windows owner. Preserve registered Git worktrees and review detached source copies, older `.a*`/short-named directories, candidate/acceptance homes, `.horizon/`, the shortcut, archives, PDFs, and the runtime state directories with the maintainer. In `install/`, the remaining candidate builds can contain registrations, data homes and evidence. The `return/` and `win_test_2026-11-09/` folders may be user handoffs. The old paths and their contents are not evidence of permission to remove them.

A second, elevated-shell pass used only same-volume atomic directory renames with distinct destinations under `tmp/legacy-240/remainders/`. Two residual directories moved (`.tmp-p5-full-8906e39` and `.tmp-voyager-final-9b0a0c0`). Windows denied the remaining 288 attempts with HRESULT 0x80070005, including 271 partially relocated paths and 17 rename-only paths. Per-path outcomes are in that directory’s `outcomes.csv`. All ten registered worktrees still resolved to their own top level. The moved cleanup-tool inputs, original user-return directory and Windows test handoff remained accessible.

## Spec 4 desktop review (#243–245)

The maintainer verified the main conversation controls, session selection, hover and focus, normal and narrow layouts, options and tray menus. The final navigation shows the conversation title and tabs only within a selected conversation. The sidebar options menu opens to the upper right. Knowledge, themes, Voyager, schedules, Console, trace and model settings passed the requested visual checks. Remaining per-page keyboard traversal and narrow-width checks were waived by the maintainer.

The former PDF reader showed page one alongside a format error and lacked visible controls. The maintainer then requested an empty Materials section and confirmed it after rebuild. Issue #246 tracks the future reference manager and former reader observation. Stored materials were retained.

The maintainer verified Windows caption controls, drag, double-click, native snapping, tray restoration and the native file chooser before the global caption was separated from conversation navigation.
After separating the caption, the maintainer confirmed dragging the global bar. The final conversation navigation contains Chat and an empty Trace tab, verified after rebuild. Issue #247 tracks the Trace redesign. The maintainer confirmed that the global bar, native caption controls and sidebar share the same color and that the lower divider is absent.

## Editor single-source report (#214) — 2026-11-23

- Passed on Windows source commit `b985b0f`: the real Editor child used DeepSeek `deepseek-flash` and a captured [IANA Example Domains page](https://www.iana.org/help/example-domains) to save report `4c8123bc-4c54-4509-94cc-d80f52f6d154` v1. The source snapshot SHA-256 was `ba3e46bd30c2ab5d120ae677a4c455073f5e05491d3fb20cd8089103a49ec457`. The saved report contained 11 Chinese claims and the `editor-default` v1 preference provenance. Each of the 21 citations reopened its exact excerpt, and the full pinned text opened through the source-reading interface.
- The user confirmed the visible Electron reading flow in the isolated fixture. They opened the **Editor 引用验收** conversation and report **4c8123bc** v1, followed a citation, and opened the original snapshot. The live model generation evidence came from the desktop worker probe.
- The 81 focused Python checks passed, along with the Agent and Electron source builds and TypeScript checks. Long sources supply a located prefix with a 42 KB budget and a displayed coverage limitation. The full pinned snapshot remains available through paged reading.

## Editor question-led synthesis (#215) — 2026-11-24

- A live Windows Editor child used DeepSeek and two RFC Editor public captures. The RFC 2026 extract SHA-256 was `6078fa55c57045bed488fd8165e0c801434945d96004a8551430c9456115ac29`. The RFC 6410 extract SHA-256 was `e754729bc0882c1352687b2c27360c19957e5432a308414cc11365cf6d129ab9`.
- Compared three saved reports on the same question. The baseline `7c7de827` had no confirmed background. After accepting a sourced Knowledge Card, `40ad3b39` recorded the exact card version and omitted expanded background. Explicit expansion `326a043e` displayed the versioned card as known background. Across the three reports, all 44 citations reopened and matched the excerpts. The documents differ on the number of maturity levels because RFC 6410 updates RFC 2026. Each report disclosed the limits of its source prefixes.
- Passed 98 focused checks, the Agent and Electron source builds, and the desktop TypeScript check. Earlier provider truncation and wrong-locator responses produced no saved report. The passing rerun covered short output and uniquely matched excerpts within the original selected source. The synthesis source context has a 14 KB total budget across readable inputs. The user confirmed visible Electron reading of all three saved reports, the different maturity levels, the expanded known-background label and citation passage opening for both RFCs. Model generation was verified through the desktop worker probe.

- A follow-up review check passed for automatically surfacing a rate-limited retrieval source from selected material provenance and suppressing that retrieval's gaps in another task. The focused report, Voyager and desktop regression run passed 57 checks.

## Editor report feedback and revisions (#216)

Windows development-machine model check: configured DeepSeek Flash used the captured IANA Example Domains page. The follow-up rejected the transfer premise against the source and marked the alleged fee and future date as unsupported, proposing official sources for further retrieval. The same report ID retained its original v1 claims and rating, v2 had no inherited rating, and all 14 v2 citations reopened their exact excerpts. The model key was removed from the isolated test home.

Related source tests: 44 passed, including late full text, replay, rating withdrawal, stale feedback and report conflicts, old-version reads, private desktop worker, scheduling and process behavior. Agent and desktop builds and desktop typecheck passed. The user used the isolated Windows Electron shortcut and confirmed version-specific rating save, the independence of v2 feedback from v1, and rating withdrawal. This visible rating check used prepared reports. Model-driven rewrites were verified separately through the desktop worker.

Scheduled supplement check: DeepSeek Flash revised a stored summary from a supplied fixture PDF in the original plan's task. The plan queue completed only after v2 was stored under the original report ID. Both v2 citations reopened the PDF passage. The plan's coverage and scheduling state were unchanged. The PDF and catalog record were test fixtures; the model call and queue were real.

Integrated synthesis rewrite: DeepSeek Flash compared the two RFC Editor snapshots recorded above and corrected the three-to-two maturity-level change in report `90107b06` v2. The original question, selected material IDs, and v1 remained unchanged. All 19 v2 citations reopened the correct snapshot (5 from RFC 2026 and 14 from RFC 6410). The isolated model credentials were removed after the run.

After combining the final synthesis coverage fix with report versioning, 101 related Python checks passed. The Agent and Electron source builds and desktop TypeScript check also passed.

## Editor writing preferences (#217) — 2026-11-24

- A real DeepSeek Editor used the same IANA Example Domains snapshot for four saved reports. The default report `742b1463` contained 6 claims and 439 prose characters. After explicit adoption of the feedback suggestion, global concise `be68ed2d` contained 4 claims and 286 characters. Frontier detailed `ae35d1d5` contained 7 claims and 666 characters. Single-request concise and conclusion-first `96461778` contained 4 claims and 404 characters. All 41 cited passages reopened and matched their excerpts. The single-request choice left the Frontier preference intact, and the original report retained its default preference provenance.
- An earlier single-request model response with an invalid locator failed validation. The successful rerun instructed Editor to preserve coherent quotations even when shortening the report prose. The probe reused the configured credentials in memory, and the GUI fixture contains only the database and source snapshots.
- The 106 focused Python checks passed, along with the Agent and Electron builds and desktop TypeScript checks. A desktop fixture was prepared for candidate adoption and preference scope display. The maintainer removed manual acceptance from this delivery, so those interactions were not executed.

- Follow-up checks passed for an inherited Frontier rewrite followed by an explicit global rewrite, a stale scope reset preserving a concurrent preference, and task-scoped Agent handoff drafts carrying feedback text, citation paragraph and Frontier version. The 76 focused checks and desktop type check passed.

- Owner routing now presents authenticated drafts in the existing Voyager scope form and Knowledge Workbench, and opens the versioned Frontier record for Explorer review. The draft initiates no provider execution or knowledge mutation. Production Keeper proposal intake is outside this preference handoff.

## Production Editor and Keeper loop (#218) — 2026-11-24

- A real Windows Editor using DeepSeek captured the [IANA Example Domains page](https://www.iana.org/help/example-domains) with extracted SHA-256 `ba3e46bd30c2ab5d120ae677a4c455073f5e05491d3fb20cd8089103a49ec457`. Task `c69ae745` kept report `a5facfe8` across v1–v4. Five Keeper children in the same task recorded the embedded-model review path after deterministic evidence checks.
- Authenticated Explorer commands approved a proposed card, rejected another while leaving knowledge unchanged, and requested an adjustment that returned revision 2 to pending. The next Editor report v3 read approved card `97da6272` v1 and displayed it as confirmed background. A v4 proposal to update that card encountered an independently saved alias edit. The stale approval applied zero changes. The real Keeper rechecked the conflict, and a new explicit approval advanced the card to v3 while retaining the manual alias.
- Source builds and TypeScript checks passed. The 77-check Editor, desktop and Keeper run passed, and separate production authority and Frontier confirmation checks passed. Scope and preference changes remain with their owning workflows. Tests used authenticated APIs and persisted state reads. The live probe did not use Computer Use.

- Review recovery passed 40 focused checks, including terminal child replay before the request receipt, idempotent explicit retry and production-only startup selection. A real revision cancelled before dispatch stayed recoverable; an authenticated explicit retry used model child `43acbc51` and published proposal revision 2. It did not apply the proposed change.


## Installed Windows Editor distribution (#219)

- On Windows 10 build 19045 x64, current-source version `0.1.0-editor.219.1` built twice from `08d2b43`. Both 21,207-file packages passed artifact verification and matched byte for byte. ZIP SHA-256 `b8aa9fefcbf84d01e93b73bf45371c9878e8532c34551bf16d3f4689511ecc67`. Manifest SHA-256 `b0c11dceb6964000c66a2306def9f154d0d405db67b889754e650211906aad54`.
- The package pinned Python 3.13.7, Node 24.16.0, Electron 45.2.3, Pi 1.5.0 and Playwright 1.65.2 with Chromium 159.0.8154.6 revision 1255. Packaging now verifies and includes Pi's [upstream MIT notice](https://github.com/earendil-works/pi/blob/v1.5.0/LICENSE), SHA-256 `0457f5bcec3b3b211605dfb5d1a49042fd638f3686a410fe099c24a25af13c48`. Nine runtime inventory and installer smoke tests passed. Compared with the earlier business verification artifact from `8247e8d`, the final manifest has exactly three differing payload paths, the build inventory module, the component inventory, and the new notice. All application and bundled runtime paths are byte-identical.
- The supported developer installation passed package install, verification, console preparation, skill pointer, bundled wrapper startup, post-wrapper verification and report under a minimum System32/SystemRoot PATH. The licensed package installed into a separate code directory and reused the original isolated data home. On two real launches, the packaged Python owner and Electron established their local bridge and neither had an attached console. Each captured four descendants. The registered Job owner and all captured descendants exited in 181 ms and 178 ms. The original SQLite FileID and seeded task survived both restarts and the change of code directory.

- Machine-observed production Electron sent its authenticated React readiness event after the renderer's startup path created the tray and menu and loaded the window. This is process and protocol evidence, not an observation of the tray's visual placement or a physical tray gesture. The workflow used no configured Feishu delivery.
- Live arXiv discovery returned eight candidates, recorded a one-query budget stop with one coverage gap and acquired a real source snapshot. Two Editor attempts on that source correctly required recovery rather than persisting unsupported excerpts. The same installed application used an independently captured IANA Example Domains source for a production report and exact citation readback, v1 score 4, rewrite v2, real Keeper approval and v3 with one known claim tied to the approved card. A subsequent real arXiv lookup carried the saved Frontier and returned eight candidates. A planned Voyager request cancelled at zero queries. A real due schedule launched and was paused. These checks do not assert a change in candidate ranking or that the scheduled report finished.
- The licensed application reopened the same business home and verified task `67a6938c`, report `14b5bb8b` v1–v3 with v1 score 4, approved proposal `810a7a57`, original arXiv request `9472793d` and one schedule, with delivery still disabled. The first full live run used the prior package with identical product files, including the normal Electron renderer and authenticated backend. A follow-up on the licensed application completed the direct arXiv discovery-to-report transition. The two real discovery requests `9472793d` and `afb484ed` supplied two acquired PDF snapshots, `d32dd6c2` and `1ca1896a`. The real Editor saved synthesis `141e0c11` v1 with three claims and two citations, one from each snapshot, and both citations reopened the exact page extract. The snapshot SHA-256 values were `fa2a3812f0067b3c2718e052b289ec37f6943aa5d74551c04e50dbcbf0f901e3` and `5b0670f9f1aac631f93e118c2b33feced9d002c5ebf3ee2a31570f4a2f1931fb`. The same academic report retained v1 score 4, was rewritten to v2, and produced an approved proposal `0f3fe9df` through real Keeper child `e1b14d9e`. Its v3 contained an existing-knowledge claim tied to the newly approved card `e95541a9`. A final licensed restart reopened the same task, both report identities with v1–v3 and exact v1 score 4, both approved proposals, original discovery receipts and the persisted paused schedule. Delivery remained disabled and isolated model configuration was absent. The earlier single-PDF responses that failed excerpt validation remain recorded as failed attempts.

- An additional licensed-package pause and continuation check completed the same live turn `fcf0c575` across two attempts. The durable turn moved from paused v2 to completed v4 with the same task, session, input and prompt hash. Closing the relay after the pause produced WinError 10053 after the pause was already durable. The resumed answer covered the requested subject but did not include the probe's optional synthetic sentinel. The successful continuation assertion covers the preserved turn and its state transition.

## Optional Feishu CLI setup (#220)

- Windows x64 development verification of `14c5930` started with a new isolated data home and an empty tool PATH. The setup service fetched the fixed official npm package and the pinned release archive and verified both before publication. The ZIP SHA-256 was `1b43cb63ff533ee067db45d93a862074d147df5a0d05f95c2d2a7c70c3f97375`. It used Node 24.16.0 from the #219 packaged runtime. The CLI reported 1.0.99. No existing CLI or credential directory was used.
- Windows process-handle checks confirmed the Node and lark-cli processes were members of the original Horizon Job. Windows also associated console-host processes with the same Job. An operating-system visibility check observed zero visible child windows. The standard desktop-process tests passed.
- The official CLI generated a PNG for the developer console URL. A non-secret fixture exercised current-user DPAPI storage and the official CLI bot-only credential projection through a dry run. The stored bytes and public status omitted the fixture secret. This did not verify a real tenant identity, permission grant, message subscription, card callback or private-chat binding.
- The 48 focused Python checks, Agent and desktop builds, and desktop TypeScript check passed. Bridge tests confirmed the sanitized setup state and validation errors. Manual acceptance was removed from this delivery scope.

The setup review follow-up distinguished file integrity from the current launch check. Windows native checks refused writes and renames to the pinned Node verifier and the staged CLI during their launch boundary. A simulated pointer publication interruption preserved the previous selected bytes and a subsequent retry installed the verified generation. The official-assets retest with the final manifest and handle checks used a new data home and completed in 1.42 seconds. It verified the packaged Node and CLI Job memberships, zero visible child windows, DPAPI and QR output with the same non-secret fixture.

The final native-path review verified no-follow directory and file creation, competing installation refusal, an unchanged external hard-linked lock sentinel, and failed attempts to rename the owned root or generation during staged file writes. The launch recheck and pointer interruption cases continued to pass. The source tests passed for the adapter, desktop bridge, and Job, with native shared-lease regressions rerun in the Windows user context. A fresh official-assets probe completed in 1.49 seconds and confirmed CLI 1.0.99, protected fixture credentials, QR output, original Job membership, and zero visible child windows.

## Library and Librarian desktop acceptance (#259)

- Windows Electron with isolated data verified native window controls, collapsible navigation, resizable details, and opening HTML, PDF, and saved reports. The migration retained legacy terms and materials, cleared temporary propositions, and allowed the first Hpedia question to be confirmed from an empty view. Bulletin Board links appeared and disappeared with term and question convergence, and opened the original records.
- The actual Pi Agent and desktop both confirmed writes in their originating interface with a deterministic local model. Verification covered confirmation buttons, durable receipts, the shared Editor/GUI lock, and releasing it when approval failed because source evidence was removed. Automated checks exercised the 72-hour deadline and repeated decisions.
- Windows Voyager received a live arXiv response and persisted its history before a deliberate stop. An isolated provider fixture covered full two-phase pagination. The desktop displayed two linked Archive sources, opened the associated PDF, retained the Frontier after deleting the final progress, and removed only the Agent-created test progress during a confirmed cleanup.
- The integrated Windows suite passed 1,770 checks across the full run and a separate short-path packaging rerun, with 15 skips. Desktop typecheck and build, and the Agent build and runtime check, passed.

The [Spec 8 issue](https://github.com/Moke110/horizon/issues/259) links the individual acceptance records.

## Term editing and shared desktop controls (#260)

- The renderer audit found the fixed input, select, checkbox, file input, disclosure and table markup in `desktop/src/gui/elements`. Both PDF chooser entry points use the shared file row and retain native Electron selection and import. The Windows smoke failed on the old PDF picker UI and passed after migration, including the selected basename and the unchanged PDF bytes sent through the bridge.
- The Windows smoke ran the built renderer in an actual BrowserWindow using the installed Electron runtime, an isolated profile and `--disable-gpu`. It exercised the six-line definition input, the three Term lists, draft and cancellation rules, keyboard and mouse, Library navigation and review, file controls, workzone resizing, native caption overlay, PDF canvas, saved HTML and report controls. The smoke uses local fixture responses for the desktop bridge and a deterministic PDF chooser return value. The operating system's chooser dialog and unrelated external services were outside that check.
- The complete Windows Python suite passed 1,770 tests with 15 skips. Desktop TypeScript checking, the desktop and Agent builds, and 12 Keeper renderer tests also passed. The Term tests cover reading records created with the retired field, alias search, and preserving the existing update, history and Librarian rules.

## Encyclopedia and whiteboard acceptance — 2026-11-26

Windows Electron checks on the integrated desktop verified question and term browsing, frontier tracking and resolution, cross-type creation, filter restoration, card status indicators and navigation from whiteboard links. Creation checks used isolated data and the Python librarian to confirm writes and read back saved entries. The card indicator checks verified computed colors, hover text and accessible descriptions. [Creation evidence](https://github.com/Moke110/horizon/issues/270#issuecomment-7186616887) · [Filter and indicator evidence](https://github.com/Moke110/horizon/issues/271#issuecomment-7186618521)

The integrated whiteboard and frontier service checks passed (7 tests), alongside desktop type checking, build and Electron smoke. Synthesis tests verified citation eligibility for tracked valid terms and exclusion of questionable terms and terms with inadequate sources. [Whiteboard evidence](https://github.com/Moke110/horizon/issues/272#issuecomment-7186833573) · [Synthesis evidence](https://github.com/Moke110/horizon/issues/273#issuecomment-7186134686)

## Hpedia ID creation and lookup — 2026-11-27 (#275)

In an isolated Windows workspace, the Librarian confirmed one term, question and frontier. A fresh Electron desktop opened each persisted entry through the Hpedia search field using its full ID, uppercase ID and hyphenated UUID. All nine detail views copied the expected canonical ID to the Windows clipboard.

## Cross-workspace Hpedia transfer (#277)

An isolated Windows Electron desktop imported a JSON file from a second workspace. Its confirmation displayed the local and incoming titles for the same ID plus an independent entry. Selecting the incoming version and confirming persisted both original identities. The desktop exported a JSON file with both verified titles and IDs. The probe supplied the file input and save-dialog destination; both data paths used the actual Librarian and SQLite store.
