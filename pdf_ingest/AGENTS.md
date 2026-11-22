# PDF ingestion and page-level evidence anchors

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | PDF ingestion: manifest -> archive raw metadata + text + page anchors. |
| [anchors.py](anchors.py) | Locate evidence quotes inside page-anchored text. |
| [cli.py](cli.py) | CLI: ``python -m pdf_ingest.cli run --manifest <path> [--limit N] [--dry-run]``. |
| [ingest.py](ingest.py) | Ingest the PDF corpus into ``archive/raw`` with page-level anchors. |

## Verification

Start with [cli.py](cli.py) to inspect pDF ingestion and page-level evidence anchors.
