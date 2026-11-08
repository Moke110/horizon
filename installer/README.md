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
