"""horizon: knowledge cards -> retrieval queries, plus rating-driven state.

Cards live in ``<运行时区>/archive/cards/<card_id>.json`` and carry
``id / label / ontology / annotation``. ``horizon.yaml`` is retired: retrieval
reads the archive directly. Rating state stays in ``state/checkpoints.yaml`` but
is keyed by card id.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from common import now, read_json, read_yaml, write_yaml
from archive.store import is_under_review

VERDICT_VALUE = {"irrelevant": 0.0, "ok": 0.5, "great": 1.0}
SOURCES = ("arxiv", "europepmc")
CARD_FIELDS = ("id", "label", "ontology", "annotation")


class HorizonError(RuntimeError):
    pass


# Temporary acceptance cards (``card_smoke_*``) live in
# the same archive as formal cards, but they are marked temporary test content,
# never knowledge. Only the temporary test's own plan may read them, so formal
# loading skips them; otherwise a running acceptance test would inject its
# keyword into the daily retrieval and push plan.
TEMPORARY_CARD_PREFIX = "card_smoke_"


def temporary_card_id(test_id: str) -> str:
    """The one place a temporary acceptance card id is built (``card_smoke_*``)."""
    suffix = str(test_id or "")
    if suffix.startswith("smoke_"):
        suffix = suffix[len("smoke_") :]
    return f"{TEMPORARY_CARD_PREFIX}{suffix}"


def is_temporary_card(card: dict | None) -> bool:
    """Whether a card row belongs to the temporary acceptance loop, not knowledge."""
    return str((card or {}).get("id") or "").startswith(TEMPORARY_CARD_PREFIX)


def load_cards(home: Path, *, include_temporary: bool = False) -> list[dict]:
    card_dir = Path(home) / "archive" / "cards"
    rows = [read_json(p) for p in sorted(card_dir.glob("*.json"))] if card_dir.exists() else []
    rows = [r for r in rows if r]
    if not include_temporary:
        rows = [r for r in rows if not is_temporary_card(r)]
    if not rows:
        raise HorizonError(f"no knowledge cards under {card_dir}")
    return rows


def load(home: Path, *, include_temporary: bool = False) -> tuple[list[dict], dict]:
    cards = load_cards(home, include_temporary=include_temporary)
    state = read_yaml(Path(home) / "state" / "checkpoints.yaml") or {
        "schema_version": 1,
        "checkpoints": {},
    }
    state.setdefault("checkpoints", {})
    return cards, state


def save_state(home: Path, state: dict) -> None:
    write_yaml(Path(home) / "state" / "checkpoints.yaml", state)


def _quote(term: str) -> str:
    return '"' + term.replace('"', '\\"') + '"'


def _term_clause(prefix: str, term: str) -> str:
    """One ontology term as a query clause.

    A multi-word term is a recall cue, not a literal phrase: requiring the
    exact phrase (``abs:"single-cell perturbation prediction benchmark"``)
    returned zero hits on both sources even though the component words matched
    hundreds of papers.  Keep the quoted phrase for a single word and OR the
    words for a multi-word concept; precision is the editor/ranking layer's job.
    """
    words = [word for word in str(term or "").split() if word]
    if not words:
        return ""
    if len(words) == 1:
        return f"{prefix}:{_quote(words[0])}"
    return "(" + " OR ".join(f"{prefix}:{_quote(word)}" for word in words) + ")"


def _arxiv_query(terms: list[str], start: str, end: str) -> str:
    clauses = [_term_clause("abs", term) for term in terms]
    body = " OR ".join(clause for clause in clauses if clause)
    s = start.replace("-", "")
    e = end.replace("-", "")
    return f"({body}) AND submittedDate:[{s}0000 TO {e}2359]"


def _epmc_query(terms: list[str], start: str, end: str) -> str:
    clauses = [_term_clause("TITLE_ABS", term) for term in terms]
    body = " OR ".join(clause for clause in clauses if clause)
    return f"({body}) AND (FIRST_PDATE:[{start} TO {end}])"


def plan(
    home: Path,
    day: str | None = None,
    days: int = 7,
    limit: int = 20,
    sources: tuple[str, ...] = SOURCES,
    include_temporary: bool = False,
) -> dict:
    cards, state = load(home, include_temporary=include_temporary)
    end = date.fromisoformat(day) if day else date.today()
    start = end - timedelta(days=max(days - 1, 0))
    # Higher-weight cards query first: the retriever dedupes by paper, so the
    # first matching query owns the candidate.
    cards = sorted(
        cards,
        key=lambda c: (
            -float(state["checkpoints"].get(c["id"], {}).get("weight", 5)),
            c["id"],
        ),
    )
    queries = []
    for card in cards:
        card_id = card["id"]
        # Under review cards are kept and counted, but never drive retrieval.
        if is_under_review(card):
            continue
        st = state["checkpoints"].get(card_id, {})
        if st.get("enabled") is False:
            continue
        terms = [t for t in card.get("ontology", []) if t]
        if not terms:
            continue
        for source in sources:
            query_id = f"q_{card_id}_{source}"
            query = (
                _arxiv_query(terms, start.isoformat(), end.isoformat())
                if source == "arxiv"
                else _epmc_query(terms, start.isoformat(), end.isoformat())
            )
            queries.append(
                {
                    "query_id": query_id,
                    "checkpoint_id": card_id,
                    "card_id": card_id,
                    "card_label": card.get("label", ""),
                    "source": source,
                    "query": query,
                    "terms": terms,
                    "annotation": card.get("annotation", ""),
                    "limit": limit,
                }
            )
    return {
        "schema_version": 2,
        "date": end.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "generated_at": now(),
        "queries": queries,
    }


def validate(home: Path, *, include_temporary: bool = False) -> dict:
    cards = load_cards(home, include_temporary=include_temporary)
    problems, rows = [], []
    for card in cards:
        missing = [f for f in CARD_FIELDS if card.get(f) in (None, "", [])]
        if missing:
            problems.append(f"{card.get('id')}: missing {', '.join(missing)}")
        rows.append(
            {
                "card_id": card.get("id"),
                "label": card.get("label", ""),
                "terms": len(card.get("ontology", []) or []),
            }
        )
    return {"ok": not problems, "cards": rows, "problems": problems}


def record(home: Path, card_id: str, verdict: str, pushed: bool = False) -> dict:
    if verdict not in VERDICT_VALUE:
        raise HorizonError(f"verdict must be one of {sorted(VERDICT_VALUE)}")
    cards, state = load(home)
    if card_id not in {c["id"] for c in cards}:
        raise HorizonError(f"unknown card: {card_id}")
    st = state["checkpoints"].setdefault(
        card_id,
        {
            "weight": 5,
            "ema": 0.5,
            "enabled": True,
            "last_checked": None,
            "hit_rate": {
                "checked": 0,
                "pushed": 0,
                "scored_total": 0,
                "scored_positive": 0,
            },
            "proposals": [],
        },
    )
    st.setdefault("hit_rate", {})
    hr = st["hit_rate"]
    for key in ("checked", "pushed", "scored_total", "scored_positive"):
        hr.setdefault(key, 0)
    hr["scored_total"] += 1
    if verdict == "great":
        hr["scored_positive"] += 1
    if pushed:
        hr["pushed"] += 1
    ema = float(st.get("ema", 0.5))
    st["ema"] = round(0.7 * ema + 0.3 * VERDICT_VALUE[verdict], 4)
    st["weight"] = max(1, min(10, round(1 + 9 * st["ema"])))
    st["last_checked"] = date.today().isoformat()
    save_state(home, state)
    return {
        "checkpoint_id": card_id,
        "card_id": card_id,
        "verdict": verdict,
        "ema": st["ema"],
        "weight": st["weight"],
        "hit_rate": hr,
    }


def show(home: Path, card_id: str | None = None) -> dict:
    cards, state = load(home)
    rows = []
    for card in cards:
        if card_id and card["id"] != card_id:
            continue
        st = state["checkpoints"].get(card["id"], {})
        rows.append(
            {
                "id": card["id"],
                "label": card.get("label", ""),
                "ontology": card.get("ontology", []),
                "enabled": st.get("enabled", True),
                "weight": st.get("weight", 5),
                "ema": st.get("ema", 0.5),
                "last_checked": st.get("last_checked"),
                "hit_rate": st.get("hit_rate", {}),
            }
        )
    return {"schema_version": 2, "cards": rows}
