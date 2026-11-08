# Building the Horizon installer

Build the Pi Agent with `npm --prefix agent ci --ignore-scripts` and
`npm --prefix agent run build`. Prepare the Feishu binary with
`node agent/node_modules/@larksuite/cli/scripts/install.js`.
Prepare bundled CPython and copy the pinned Node.js node.exe into that runtime directory.
Build a distribution using hostrun.distribution and its --runtime-dir option.

Pass the resulting package directory to Inno Setup:

```powershell
./installer/build.ps1 -ISCC 'C:/tools/inno/ISCC.exe' -PackageDirectory './dist/horizon-0.1.0-preview.1-prerelease'
```

The installer includes the application, Pi, Node.js, Python and the Feishu CLI.
The sole `Horizon.lnk` launcher lives inside the Horizon installation directory and opens the TypeScript desktop. No Start menu or outer desktop launcher is created.
Run `/horizon-init` to configure the model API key and bind Feishu.
Zig and the previous Hello World executable are no longer used.

The installer does not authenticate or send a message during installation.
Clean Windows installation and live model/Feishu acceptance require separate verification.
