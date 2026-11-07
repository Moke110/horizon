# Desktop acceptance

The source-checkout launcher opens `hostrun.desktop` using pythonw. Native Tk widgets integrate the existing Console reader/output seams and the embedded Pi event stream. No release tag was published; packaged CPython still needs Tkinter before desktop distribution can be supported.

Validation: 49 focused tests passed, including native Tk widget creation, session persistence, streamed event rendering, sidebar toggling, Console initialization and embedded Pi integration. The sandbox full suite had 923 passed, 82 failed, 3 skipped; failures include missing platform architecture in distribution compatibility checks. The unrestricted Windows full suite subsequently passed: 1007 passed, 3 skipped, with two cache-write warnings.

Live acceptance remains pending: automated window inventory did not expose the launched Frontier window. Actual model-provider conversation and interactive Feishu workflows were not exercised. Do not interpret source deployment or focused tests as live acceptance.


Startup recovery: removed the two confirmed desktop test processes left running on an inaccessible desktop. The Explorer-launched window was observed through Windows UI Automation as `Frontier — Research Agent`. Repeated launches now request deiconify/lift/focus via an activation file; a Tk regression test verifies restoration from a withdrawn window. Actual model-provider and Feishu acceptance remain pending.

## Windows desktop process lifecycle — 2026-11-07

- Terminated the identified pre-existing Frontier GUI tree (34728 / 36608 and children), plus all diagnostic GUI instances created during this change.
- Installed local Frontier.lnk entries in checkout, parent folder and current-user Start Menu. They target pythonw directly, with no shell wrapper. No global security policy was changed; file-level Unblock-File applied only to launcher/runtime targets.
- 52 focused tests passed: desktop, real Windows normal/forced job cleanup, child GetConsoleWindow == 0, Console, initialization and embedded Agent. Pytest cache emitted one permissions warning.
- Interactive desktop shortcut launch/close acceptance remains pending: Explorer automation had unreliable focus; no successful visible GUI launch was observed in this verification. Do not treat process startup alone as visible GUI acceptance.
- Native .cmd is retained for CLI compatibility and may flash its own console; the supported zero-console entry is Frontier.lnk / Start Menu Frontier.


### TypeScript desktop migration — 2026-11-07

React/Electron assets build and typecheck. The desktop worker, process ownership, agent, distribution and release checks pass (102 tests). The Tkinter desktop and preview routing have been removed. Only `<code>/Frontier.lnk` is generated; outer and Start Menu shortcuts were removed from the local installation.

Live acceptance remains blocked: Electron 45.2.3 aborts before application JavaScript with `0x80000003`. Native debugging captured sandbox initialization return value 12 (`SBOX_ERROR_CANNOT_CREATE_WINSTATION`); both direct and desktop-helper launch failed. No sandbox or Windows security settings were changed. Rendering, live model interaction, and complete GUI shutdown acceptance remain unverified. Resident business services now wait for the renderer ready handshake.

### Electron startup repair — 2026-11-07

The earlier startup blocker is resolved locally. The same Electron 45.2.3 payload failed with 0x80000003 on the E: workspace volume but passed normal sandboxed startup from the user's C: local application directory. The launcher now atomically caches the bundled runtime under LOCALAPPDATA/Frontier/desktop-runtime, keyed by all runtime file contents. No sandbox or Windows security policy is disabled.

Two additional startup defects were fixed: a console-free Electron process treated stdin as EOF and quit before displaying its window; the generic child policy applied SW_HIDE to the GUI. The bridge now uses a per-launch token-authenticated loopback socket, and the visible GUI child retains CREATE_NO_WINDOW and Windows job containment without SW_HIDE.

Live checks: the outer `启动 Frontier.lnk` opened a visible Frontier window, and launching it twice retained the same main-window PID. Accessibility inspection confirmed the welcome screen and controls; an Electron renderer smoke test exercised settings, cancel and Console and captured `.frontier/smoke.png`. Alt+F4 closed the actual shortcut-launched GUI, and a subsequent process inspection found no Frontier Electron or desktop Python processes. Nine focused desktop tests passed, including real normal/forced Windows job cleanup. Live provider and Feishu acceptance remain outside this startup repair.

Remote fetch was rejected by automatic approval review; this repair remains local and has not been pushed.

Extended verification: 77 related tests passed together; the remaining real-checkout distribution test passed separately using a shorter temporary path (the initial failure exceeded Windows path limits). Desktop typecheck and build both passed. A final visible GUI was reopened for the user.

Integration follow-up: the maintainer explicitly authorized merging and pushing to main. Fetch succeeded with dev and origin/main at the same baseline. Pre-merge desktop, release and documentation checks passed (99 tests), along with desktop typecheck and build.
