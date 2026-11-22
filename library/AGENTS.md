# Research PDF library management, reconciliation and search

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | Lark library: source PDFs and push artifacts live in Drive, registered in Base. |
| [admin.py](admin.py) | Administrative lark-cli calls that do not belong to one Base yet. |
| [base.py](base.py) | Thin lark-cli wrappers for the archive tables and Drive folders. |
| [cli.py](cli.py) | library CLI: Drive blobs, Base registration, quota, reconcile, cleanup. |
| [config.py](config.py) | Runtime settings: Base 「配置」 row is the only source of truth. |
| [drive.py](drive.py) | Drive abstraction: a real lark-cli drive and an in-memory fixture. |
| [offline.py](offline.py) | Persisted offline Drive + Base for ``--offline`` and local demos. |
| [service.py](service.py) | Library service: source PDFs and push artifacts in Drive, registered in Base. |
| [setup.py](setup.py) | Create the 原文 / 推送日志 / 决定 / 配置 tables and the two Drive folders. |

## Verification

Start with [cli.py](cli.py) to inspect research PDF library management, reconciliation and search.
