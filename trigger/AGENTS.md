# Keyword matching and trigger evaluation

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | Manual Lark trigger for one forward horizon run. |
| [cli.py](cli.py) | trigger CLI: listen for Lark keywords and run one manual push. |
| [keywords.py](keywords.py) | Parse the manual-push keywords sent from Lark. |
| [service.py](service.py) | Manual-push listener: Lark message -> one forward pipeline run. |

## Verification

Start with [cli.py](cli.py) to inspect keyword matching and trigger evaluation.
