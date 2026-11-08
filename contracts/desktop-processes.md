# Desktop process ownership

The user-started GUI owns every background process it creates. No worker may outlive it.

- Before Console/Agent workers start, attach the GUI to a non-inheritable Windows Job Object with KILL_ON_JOB_CLOSE. Descendants inherit job membership; breakaway is prohibited. Failure to establish containment must abort startup.
- The GUI retains the only job handle. Normal exit, crash, and forced termination close that handle and Windows terminates the entire descendant tree. No polling daemon or machine-wide process-name kill is used.
- Closing the window stops accepting new requests, cancels the active agent, signals Console EOF, and allows at most 10 seconds for graceful shutdown. Then the GUI process exits, including if worker threads remain.
- All Python subprocess launches in desktop mode hide their windows. The policy is inherited by hostrun CLI workers through HORIZON_DESKTOP. Node subprocess calls must set windowsHide: true. New process launch sites must preserve both containment and hidden-window behavior.
- The Electron GUI child uses CREATE_NO_WINDOW without SW_HIDE, so the app window remains visible while no console is allocated. It remains in the owner's job. Python and Electron exchange newline-delimited JSON over a randomly allocated loopback port authenticated by a per-launch random token; standard-input EOF is not used as the GUI lifetime signal.
- On Windows, deploy the bundled Electron binaries atomically to a content-addressed cache under LOCALAPPDATA/Horizon/desktop-runtime before launch. Keep application code and user state in their original locations, and retain the renderer sandbox. Never add --no-sandbox as a startup fallback.
- No detached services, scheduled tasks, startup registrations, or breakaway flags may be added for desktop workers. Standalone CLI sessions have their own explicit lifecycle.
- Use the locally generated Horizon.lnk / Start Menu entry for zero-console startup. A .cmd file necessarily invokes cmd.exe and remains a CLI compatibility entry.
- Cleanup of pre-existing processes must identify the exact Horizon invocation and descendants; never kill all python/node/cmd processes by name. Do not delete locks, logs, or archive data as a substitute for stopping their owners.
- Local shortcut installation removes download blocking only from the launcher/runtime files used by the shortcut. It must not disable SmartScreen, Defender, UAC, or global attachment policy.

Verification: tests/test_desktop_processes.py runs isolated real Windows job owners and checks cleanup after both normal and forced exit. GUI startup/close and zero remaining Horizon descendants must also be checked on the interactive desktop.
