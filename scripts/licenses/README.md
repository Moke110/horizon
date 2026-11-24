# Vendored runtime notices

`node-24.16.0.txt` is the unchanged Node.js v24.16.0 license and third-party
notices from https://raw.githubusercontent.com/nodejs/node/v24.16.0/LICENSE.
SHA-256: `4573185d56580da2b890ba34a85a409257640f1c5632eade4300137266194d18`.
The build preparation copies it into the prepared runtime. Python's notices
come from its official embeddable archive; npm and wheel metadata/notices are
retained and indexed from the package contents.


`pi-1.5.0.txt` is the unchanged MIT notice from the
[Pi v1.5.0 source tag](https://github.com/earendil-works/pi/blob/v1.5.0/LICENSE).
Its LF byte SHA-256 is `0457f5bcec3b3b211605dfb5d1a49042fd638f3686a410fe099c24a25af13c48`.
The distribution builder verifies the digest, copies the notice into the runtime
and references it in the pinned Pi coding-agent component inventory.
