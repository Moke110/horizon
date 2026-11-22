# Read and migrate the local research archive

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | Python package initialization. |
| [cli.py](cli.py) | archive CLI — the only writer of the runtime area's archive store. |
| [migrate.py](migrate.py) | One-shot v1 → v2 migration for the knowledge store. |
| [store.py](store.py) | archive: the only writer of the knowledge store. |

## Verification

Start with [cli.py](cli.py) to inspect read and migrate the local research archive.
