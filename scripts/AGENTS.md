# Developer builds, compatibility probes and release tooling

| File | Responsibility |
|---|---|
| [build_initial_theme_sample.py](build_initial_theme_sample.py) | Build the draft initial-build sample for the evaluation-credibility theme. |
| [build_theme_inventory.py](build_theme_inventory.py) | Cluster the PDF manifest into initial-build themes. |
| [build_update_card_samples.py](build_update_card_samples.py) | Regenerate the update-question card samples under fixtures/update-cards/. |
| [ci_install_smoke.py](ci_install_smoke.py) | Install a built distribution artifact into a clean tree and prove it works. |
| [demo.py](demo.py) | Offline end-to-end demo: the current pipeline, file contracts only. |
| [install-desktop.ps1](install-desktop.ps1) | install desktop (Developer builds, compatibility probes and release tooling). |
| [mac_lifecycle_probe.py](mac_lifecycle_probe.py) | Native candidate experiment, never production containment or acceptance. |
| [migrate_drop_test_push_refs.py](migrate_drop_test_push_refs.py) | One-time migration: drop references to test-phase pushes (ADR 0003, Q13). |
| [prepare_browser.py](prepare_browser.py) | Install official pinned Chromium into an explicit package directory, inventory all bytes. |
| [prepare_cleanup_tool.py](prepare_cleanup_tool.py) | Offline development artifact, not the complete Windows acceptance kit. |
| [prepare_runtime.py](prepare_runtime.py) | Assemble the bundled CPython runtime a release package ships. |
| [prepare_windows_acceptance.py](prepare_windows_acceptance.py) | Assemble a local, manually transferred Windows installation test kit. |
| [probe_voyager_academic.py](probe_voyager_academic.py) | Run with python -m scripts.probe_voyager_academic from the source checkout. |
| [probe_voyager_web.py](probe_voyager_web.py) | Verify four live public source types through the Materials service. |
| [update_release_index.py](update_release_index.py) | Merge one release into ``releases/index.json`` deterministically. |

## Subdirectories

- [licenses](licenses/AGENTS.md) — Third-party license texts for packaged tools.
- [mac_supervision_prototype](mac_supervision_prototype/AGENTS.md) — macOS process-supervision prototype and operator guide.

## Verification

Start with [build_initial_theme_sample.py](build_initial_theme_sample.py) to inspect developer builds, compatibility probes and release tooling.
