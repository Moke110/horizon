# Publication, alerts and interactive delivery cards

| File | Responsibility |
|---|---|
| [__init__.py](__init__.py) | Python package initialization. |
| [alerts.py](alerts.py) | Deliver library alerts as copyable owner DMs. |
| [cards.py](cards.py) | Card payload builders for the publisher prototype (Card 2.0). |
| [cli.py](cli.py) | publisher CLI: Feishu push, ratings, approvals. |
| [feishu.py](feishu.py) | Thin lark-cli wrapper. Only module that talks to Feishu. |
| [service.py](service.py) | Publisher service: send cards, ingest callbacks, stage approvals. |
| [store.py](store.py) | Publisher-local state: message routing, ratings, pending proposals, decisions. |
| [update_cards.py](update_cards.py) | Lark cards for the unified question queue: one card = one entity update. |

## Verification

Start with [cli.py](cli.py) to inspect publication, alerts and interactive delivery cards.
