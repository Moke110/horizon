"""writer: selection -> push + knowledge-update proposals.

Deterministic extractive prototype. The only "intelligence" is sentence
selection + simple keyword matching against knowledge cards. Replace
`_summarise` / `_propose` with an LLM later; the output contract stays.
"""

from __future__ import annotations

import re
from pathlib import Path

from archive.store import Archive, is_under_review, v3_prop_from_v2
from common import clip, now, read_json, write_json
from horizon.model import is_temporary_card

SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")

METHOD_CUES = (
    "we ",
    "here ",
    "using ",
    "developed",
    "performed",
    "generated",
    "profiled",
    "screened",
    "knockdown",
    "knockout",
    "crispr",
)
RESULT_CUES = (
    "show",
    "find",
    "found",
    "demonstrate",
    "reveal",
    "observe",
    "suggest",
    "increase",
    "decrease",
    "improve",
    "predict",
    "associated",
)
KEEP_CUES = ("conclusion", "implication", "provide", "enable", "future", "suggest")


def _sentences(text: str) -> list[str]:
    parts = [s.strip() for s in SENTENCE_RE.split(text or "") if s.strip()]
    return parts or [clip(text or "", 200)]


def _pick(sentences: list[str], cues: tuple[str, ...], fallback: int) -> str:
    for sentence in sentences:
        low = sentence.lower()
        if any(cue in low for cue in cues):
            return sentence
    return sentences[min(fallback, len(sentences) - 1)]


def _summarise(abstract: str) -> dict:
    sentences = _sentences(abstract)
    did = _pick(sentences, METHOD_CUES, 0)
    found = _pick([s for s in sentences if s != did] or sentences, RESULT_CUES, 0)
    keep = _pick(list(reversed(sentences)), KEEP_CUES, 0)
    return {
        "did": clip(did, 400),
        "found": clip(found, 400),
        "keep": clip(keep, 300),
    }


def _cards(home: Path) -> list[dict]:
    card_dir = Path(home) / "archive" / "cards"
    rows = [read_json(p) for p in sorted(card_dir.glob("*.json"))] if card_dir.exists() else []
    # Temporary acceptance cards are not formal context; keep the same rule as
    # horizon.model.load_cards (#55).  Under review cards are kept but excluded
    # from writing context (#88).
    return [
        r
        for r in rows
        if r and not is_temporary_card(r) and not is_under_review(r)
    ]


def _context(home: Path, card_id: str | None) -> dict:
    root = Path(home) / "archive"
    card = None
    if card_id:
        card = read_json(root / "cards" / f"{card_id}.json")
        if card and is_under_review(card):
            card = None
    prop_dir = root / "propositions"
    props = []
    for path in sorted(prop_dir.glob("*.json")) if prop_dir.exists() else []:
        prop = v3_prop_from_v2(read_json(path) or {})
        if is_under_review(prop):
            continue
        if card_id and card_id in prop.get("related_knowledge", []):
            props.append(prop)
    raw_dir = Archive(Path(home)).raw
    papers = list(raw_dir.glob("*.json")) if raw_dir.exists() else []
    archived = 0
    for path in papers:
        rec = read_json(path) or {}
        if (rec.get("card_id") or rec.get("checkpoint_id")) == card_id:
            archived += 1
    return {"card": card, "props": props, "archived": archived, "cards": _cards(home)}


def _why(context: dict) -> str:
    card = context["card"]
    parts = []
    if card and card.get("annotation"):
        parts.append(card["annotation"].strip().rstrip("。."))
    for prop in context["props"][:2]:
        parts.append(f"命题「{(prop.get('title') or prop.get('review','')).rstrip('。.')}」")
    if context["archived"]:
        parts.append(f"该知识卡片已归档 {context['archived']} 篇，本篇是其中之一")
    return clip("；".join(parts) or "暂无知识卡片脉络，本篇作为该卡片的新线索。", 400)


def _propose(push: dict, paper: dict, context: dict, card_id: str) -> list[dict]:
    """Heuristic proposals in the v2 contract. Every one is a *proposal*."""
    proposals: list[dict] = []
    abstract = paper.get("abstract", "")
    low = abstract.lower()
    counter = 0

    def add(
        op: str,
        target_type: str,
        target_id: str | None,
        path: str,
        value,
        before,
        after,
        why: str,
        quote: str,
        conf: float,
    ) -> None:
        nonlocal counter
        quote = clip(quote, 400)
        if len(quote.strip()) < 8:
            return
        counter += 1
        evidence = {
            "paper_id": paper.get("paper_id"),
            "page": None,
            "quote": quote,
            "section": "abstract",
        }
        proposals.append(
            {
                "schema_version": 2,
                "proposal_id": f"p_{push['push_id']}_{counter:02d}",
                "push_id": push["push_id"],
                "op": op,
                "target_type": target_type,
                "target_id": target_id,
                "diff": {"op": "add", "path": path, "value": value},
                "before": before,
                "after": after,
                "rationale": why,
                "confidence": conf,
                "evidence": evidence,
                "status": "draft",
            }
        )

    # 1) add evidence to the most relevant existing proposition
    best, best_hits, best_sentence = None, 0, ""
    for prop in context["props"]:
        hits = 0
        for related in prop.get("related_knowledge", []):
            other = next((c for c in context["cards"] if c.get("id") == related), None)
            if not other:
                continue
            terms = [other.get("label", "")] + list(other.get("ontology", []))
            hits += sum(1 for term in terms if term and term.lower() in low)
        if hits > best_hits:
            best, best_hits = prop, hits
            for sentence in _sentences(abstract):
                if any(
                    term and term.lower() in sentence.lower()
                    for term in [other.get("label", "") for other in context["cards"]]
                    if term
                ):
                    best_sentence = sentence
                    break
    if best and best_hits:
        current_paper = paper.get("paper_id")
        already = [
            row
            for entry in best.get("evidence", [])
            for row in entry.get("papers", [])
            if row.get("paper_id") == current_paper
        ]
        if not already and best_sentence:
            count = len(best.get("evidence", []))
            add(
                "add_proposition_evidence",
                "proposition",
                best["id"],
                "evidence",
                {
                    "conclusion": clip(best_sentence, 200),
                    "stance": "supports",
                    "papers": [
                        {
                            "paper_id": paper.get("paper_id"),
                            "page": None,
                            "quote": clip(best_sentence, 400),
                        }
                    ],
                },
                count,
                count + 1,
                f"摘要中出现 {best_hits} 个与该命题关联卡片相关的术语，可作为新证据。",
                best_sentence,
                0.7,
            )

    # 2) propose a new proposition when two existing cards co-occur in >=2 sentences
    support = []
    hit_ids: list[str] = []
    for sentence in _sentences(abstract):
        low_sentence = sentence.lower()
        hit_cards = []
        for candidate in context["cards"]:
            terms = [candidate.get("label", "")] + list(candidate.get("ontology", []))
            if any(term and term.lower() in low_sentence for term in terms):
                hit_cards.append(candidate)
        if len(hit_cards) < 2:
            continue
        ids = [c["id"] for c in hit_cards[:2]]
        if ids[0] == ids[1]:
            continue
        linked = any(
            set(ids).issubset(set(p.get("related_knowledge", [])))
            for p in context["props"]
        )
        if linked:
            continue
        hit_ids = ids
        support.append(sentence)
    if len(support) >= 2:
        title = clip(support[0], 60)
        review = clip(" ".join(support[:2]), 2000)
        entries = [
            {
                "stance": "supports",
                "conclusion": clip(sentence, 200),
                "papers": [
                    {
                        "paper_id": paper.get("paper_id"),
                        "page": None,
                        "quote": clip(sentence, 400),
                    }
                ],
            }
            for sentence in support[:2]
        ]
        add(
            "create_proposition",
            "none",
            None,
            "proposition",
            {
                "title": title,
                "review": review,
                "related_knowledge": hit_ids,
                "evidence": entries,
            },
            None,
            {"title": title, "review": review, "related_knowledge": hit_ids},
            "至少两句话同时提到两张知识卡片，且现有命题未连接它们。",
            support[0],
            0.5,
        )
    return proposals


def write(selection: dict, home: Path, out_dir: Path, day: str | None = None) -> dict:
    home = Path(home)
    day = day or selection.get("date")
    pushes = []
    for index, row in enumerate(selection.get("selected", []), start=1):
        paper = row["paper"]
        cp_id = row["checkpoint_id"]
        context = _context(home, cp_id)
        card = context["card"] or {}
        label = card.get("label") or cp_id
        title = f"{label}：{clip(paper.get('title', ''), 60)}"
        sections = _summarise(paper.get("abstract", ""))
        sections["why"] = _why(context)
        push = {
            "schema_version": 1,
            "format": "html",
            "push_id": f"{day}_{cp_id}_{index:02d}",
            "date": day,
            "checkpoint_id": cp_id,
            "card_id": cp_id,
            "paper_ids": [paper.get("paper_id")],
            "title": title,
            "sections": sections,
            "context": {
                "card_id": card.get("id"),
                "propositions": [p["id"] for p in context["props"]],
                "archived_count": context["archived"],
            },
            "source": paper.get("source"),
            "is_preprint": bool(paper.get("is_preprint")),
            "url": paper.get("url"),
            "status": "written",
            "created_at": now(),
        }
        push["proposals"] = _propose(push, paper, context, cp_id)
        push["body_md"] = _render(push, paper)
        pushes.append(push)

    out_dir = Path(out_dir)
    (out_dir / "pushes").mkdir(parents=True, exist_ok=True)
    for push in pushes:
        write_json(out_dir / "pushes" / f"{push['push_id']}.json", push)
    return {"ok": True, "count": len(pushes), "out_dir": str(out_dir), "pushes": pushes}


def _render(push: dict, paper: dict) -> str:
    badge = "预印本（未经同行评审）" if push.get("is_preprint") else "同行评审"
    return "\n".join(
        [
            f"### {push['title']}",
            f"<font color='grey'>{badge} · {paper.get('source','')} · {paper.get('published') or '日期未知'}</font>",
            "",
            f"**做了什么** {push['sections']['did']}",
            "",
            f"**发现** {push['sections']['found']}",
            "",
            f"**为什么重要** {push['sections']['why']}",
            "",
            f"**保留** {push['sections']['keep']}",
        ]
    )
