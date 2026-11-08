# Building the Horizon installer

Build the Pi Agent with `npm --prefix agent ci --ignore-scripts` and
`npm --prefix agent run build`.
Prepare bundled CPython and copy the pinned Node.js node.exe into that runtime directory.
Record and verify the runtime versions and bundled notices with
`python -m hostrun.runtime_inventory --runtime <runtime> --node-license scripts/licenses/node-24.16.0.txt`.
Build a distribution using hostrun.distribution and its --runtime-dir option.

Pass the resulting package directory to Inno Setup:

```powershell
./installer/build.ps1 -ISCC 'C:/tools/inno/ISCC.exe' -PackageDirectory './dist/horizon-0.1.0-preview.1-prerelease'
```

Keep the distribution zip beside its identically named package directory. The installer embeds both the bootstrap runtime and the verified zip. It runs the new bundled Python from a temporary directory, so replacing the installed runtime never overwrites the executing interpreter. Close Horizon before upgrading; an active data-home lock blocks the upgrade.

The installer includes the application, Pi, Node.js and Python.
The sole `Horizon.lnk` launcher lives inside the Horizon installation directory and opens the TypeScript desktop. No Start menu or outer desktop launcher is created.
Configure the model in the desktop settings. Feishu is optional.
Zig and the previous Hello World executable are no longer used.

The installer does not authenticate or send a message during installation.
Clean Windows installation and live model/Feishu acceptance require separate verification.


For a manually transferred installation test kit, run
`python scripts/prepare_windows_acceptance.py --baseline <baseline-setup.exe> --upgrade <new-setup.exe> --output <new-directory>`.
The kit contains numbered install/upgrade wrappers that enable Inno's `/LOG`, a collector and instructions.
All collected files are under its `report/` directory. The collector uses the installed bundled Python when available;
if installation failed before that runtime exists, it still records OS/build information and retains the installer logs.
It does not change PowerShell execution policy, collect configuration/database/transcript files or upload reports.
Use the default per-user installation location for these wrappers. Preserve the report folder between steps.
