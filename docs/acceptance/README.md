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
