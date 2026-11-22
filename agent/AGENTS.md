# Embedded Pi runtime and Node build configuration

| File | Responsibility |
|---|---|
| [package-lock.json](package-lock.json) | Pins the resolved Node dependency graph. |
| [package.json](package.json) | Declares Node dependencies and build commands. |
| [tsconfig.json](tsconfig.json) | Stores tsconfig fields including compilerOptions, include. |

## Subdirectories

- [src](src/AGENTS.md) — Task execution, tool requests and provider retry policy.

## Verification

Start with [package.json](package.json) to inspect embedded Pi runtime and Node build configuration.
