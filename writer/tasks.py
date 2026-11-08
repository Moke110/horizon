"""Agent-facing writer tasks: build a writing packet, validate the pushes.

The agent writes the push and the knowledge updates. This module never calls a
model. Proposal contract: `contracts/agent-tasks.md` §2.2.
"""

from __future__ import annotations

import re
from pathlib import Path

from common import now, read_json
from archive.store import Archive, is_under_review
from horizon.model import is_temporary_card
from writer.model import _context
from writer.render import inspect as inspect_pdf

INSTRUCTIONS = """你是知识循环的写作者。对每个 item 写一篇长文推送（push JSON，format=html）：
1) 先运行 figures 命令取图：python -m writer.cli figures --selection <selection.json> --out <outbox>；
   它会把原文图或生成图落到 outbox/assets/<push_id>/，并返回 assets 列表；
2) 七个 sections 都必填：takeaway / background / methods / results / why / limits / sources；
   中文 1500–2500 字，克制、具体；「why」必须引用 context 里的知识卡、命题、或归档篇数；
3) body_html 是文章正文片段（HTML），不是整页，也不要写 <style>/<script>/CSS；
   用 <h2>、<h3>、<p>、<ul>、<table>、<blockquote> 组织内容；
4) 图片用占位符 <figure data-asset="fig-1"></figure>，渲染器会替换成本地图片与图注；
   每个占位必须在 assets 里有对应项，至少 1 张图；
5) assets 直接使用 figures 命令的输出；source=paper|generated，caption 必须写清来源；
   generated 图不能伪装成原文图；
6) sections.sources 必须包含原文 URL；paper 元数据（authors / journal / published / url）要保留；
7) 如果原文足以更新知识卡或命题，在 proposals 里提出，op 只能是：
   add_card_ontology / update_card_annotation / create_card /
   create_proposition / update_proposition_title / update_proposition_review /
   add_proposition_evidence / merge_proposition_evidence /
   update_proposition_related_knowledge；
   每条都要有 diff / before / after / rationale / confidence / evidence；
   证据条目：conclusion ≤200 字、stance=supports|contradicts、papers[] 每篇带 paper_id/page/quote；
   同立场且结论同向用 merge_proposition_evidence，否则 add_proposition_evidence；
   新建命题必须带 ≥2 条证据；review ≤2000 字；
8) 每篇推送最多 2 个实体目标（知识卡片 + 命题合计；新建也占名额；同一目标的多个操作合并成一个更新题）；
9) proposals 一律 status=draft，绝不直接改 archive。
输出目录 outbox/pushes，一个 push 一个文件，文件名 = push_id。
渲染：python -m writer.cli render --push outbox/pushes/<push_id>.json --out <pdf>。"""

CARD_OPS = {"add_card_ontology", "update_card_annotation", "create_card"}
PROP_OPS = {
    "create_proposition",
    "update_proposition_title",
    "update_proposition_review",
    "add_proposition_evidence",
    "merge_proposition_evidence",
    "update_proposition_related_knowledge",
}
PROPOSAL_KINDS = {
    "add_card_ontology": "给现有卡片加 ontology 词：target_type=card，diff.path=ontology",
    "update_card_annotation": "改写卡片 annotation：target_type=card，diff.path=annotation",
    "create_card": "新建卡片：target_type=none，value=label+ontology+annotation",
    "create_proposition": "新建命题：target_type=none，value=title+review+related_knowledge（≥2 张已有卡）+evidence（≥2 条）",
    "update_proposition_title": "改命题标题：target_type=proposition，diff.path=title",
    "update_proposition_review": "改写综述：target_type=proposition，diff.path=review（仅综述题使用）",
    "add_proposition_evidence": "新建证据条目：value={conclusion≤200, stance, papers[]}",
    "merge_proposition_evidence": "并入已有证据：value={target_evidence_id, papers[], conclusion?}",
    "update_proposition_related_knowledge": "改命题关联卡片：target_type=proposition，diff.path=related_knowledge",
}
MAX_TARGETS = 2
PROPOSAL_ID_RE = re.compile(r"^p_[a-z0-9_-]+$")
CARD_ID_RE = re.compile(r"^card_[a-z0-9][a-z0-9_]*$")
PROP_ID_RE = re.compile(r"^prop_[a-z0-9][a-z0-9_]*$")


def _cards(home: Path) -> dict[str, dict]:
    card_dir = Path(home) / "archive" / "cards"
    rows = [read_json(p) for p in sorted(card_dir.glob("*.json"))] if card_dir.exists() else []
    return {
        r["id"]: r
        for r in rows
        if r
        and r.get("id")
        and not is_temporary_card(r)
        and not is_under_review(r)
    }


def build_task(home: Path, selection: dict, date: str) -> dict:
    home = Path(home)
    cards = _cards(home)
    items = []
    for index, row in enumerate(selection.get("selected", []), start=1):
        paper = row.get("paper") or {}
        cp_id = row.get("checkpoint_id")
        card = cards.get(cp_id, {})
        ctx = _context(home, cp_id)
        items.append(
            {
                "checkpoint_id": cp_id,
                "card_id": cp_id,
                "suggested_push_id": f"{date}_{cp_id}_{index:02d}",
                "card": {
                    "id": card.get("id"),
                    "label": card.get("label"),
                    "annotation": card.get("annotation", ""),
                    "ontology": card.get("ontology", []),
                },
                "paper": paper,
                "context": {
                    "card": ctx["card"],
                    "propositions": ctx["props"],
                    "archived_count": ctx["archived"],
                    "recent_papers": [
                        {
                            "paper_id": rec.get("paper_id"),
                            "title": rec.get("title", ""),
                            "published": rec.get("published"),
                        }
                        for rec in (
                            read_json(p) or {}
                            for p in sorted(Archive(home).raw.glob("*.json"))
                        )
                        if (
                            (rec.get("card_id") or rec.get("checkpoint_id")) == cp_id
                        )
                    ][:5],
                    "cards": ctx["cards"],
                },
            }
        )
    return {
        "schema_version": 2,
        "kind": "writer-task",
        "format": "html",
        "date": date,
        "generated_at": now(),
        "instructions": INSTRUCTIONS,
        "archive_cli": "python -m archive.cli",
        "figure_command": "python -m writer.cli figures --selection <selection.json> --out <outbox>",
        "render_command": "python -m writer.cli render --push outbox/pushes/<push_id>.json --out <pdf>",
        "proposal_kinds": PROPOSAL_KINDS,
        "limits": {
            "targets": MAX_TARGETS,
            "per_entity": 1,
        },
        "evidence_rules": {
            "paper_id": "must be one of push.paper_ids",
            "page": "integer >= 1, or null when the quote is from the abstract",
            "quote": "verbatim substring of the paper text/abstract, 8..500 chars",
        },
        "items": items,
        "output": {
            "dir": "outbox/pushes",
            "filename": "<push_id>.json",
            "schema": "contracts/README.md §5.1 + §9",
        },
    }


def _target_sets(task: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    props, cards = {}, {}
    for item in task.get("items", []):
        ctx = item.get("context", {})
        card = ctx.get("card") or {}
        if card.get("id"):
            cards[card["id"]] = card
        for prop in ctx.get("propositions", []):
            if prop.get("id"):
                props[prop["id"]] = prop
        for other in ctx.get("cards", []):
            if other.get("id"):
                cards[other["id"]] = other
    return props, cards


def _evidence_count(prop: dict) -> int:
    return len(prop.get("evidence") or [])


def _validate_evidence(evidence, paper_ids: list[str], where: str, pid: str) -> list[str]:
    errors = []
    if not isinstance(evidence, dict):
        return [f"{where}: proposal {pid} needs an evidence object"]
    paper_id = evidence.get("paper_id")
    if paper_id not in paper_ids:
        errors.append(f"{where}: proposal {pid} evidence.paper_id {paper_id!r} is not the pushed paper")
    page = evidence.get("page")
    if page is not None and (not isinstance(page, int) or page < 1):
        errors.append(f"{where}: proposal {pid} evidence.page must be a positive integer or null")
    quote = evidence.get("quote")
    if not isinstance(quote, str) or not (8 <= len(quote.strip()) <= 500):
        errors.append(f"{where}: proposal {pid} evidence.quote must be 8..500 characters")
    return errors


def _validate_paper(paper, paper_ids: list[str], where: str, pid: str) -> list[str]:
    errors = []
    if not isinstance(paper, dict):
        return [f"{where}: proposal {pid} evidence paper must be an object"]
    paper_id = paper.get("paper_id")
    if paper_id not in paper_ids:
        errors.append(
            f"{where}: proposal {pid} evidence.paper_id {paper_id!r} is not the pushed paper"
        )
    page = paper.get("page")
    if page is not None and (not isinstance(page, int) or page < 1):
        errors.append(f"{where}: proposal {pid} evidence.page must be a positive integer or null")
    quote = paper.get("quote")
    if not isinstance(quote, str) or not (8 <= len(quote.strip()) <= 500):
        errors.append(f"{where}: proposal {pid} evidence.quote must be 8..500 characters")
    return errors


def _validate_evidence_entry(
    entry, paper_ids: list[str], where: str, pid: str, index: int
) -> list[str]:
    errors = []
    if not isinstance(entry, dict):
        return [f"{where}: proposal {pid} evidence[{index}] must be an object"]
    conclusion = entry.get("conclusion")
    if not isinstance(conclusion, str) or not (1 <= len(conclusion.strip()) <= 200):
        errors.append(f"{where}: proposal {pid} evidence[{index}] conclusion must be 1..200 chars")
    stance = entry.get("stance")
    if stance not in ("supports", "contradicts"):
        errors.append(f"{where}: proposal {pid} evidence[{index}] stance must be supports or contradicts")
    papers = entry.get("papers")
    if not isinstance(papers, list) or not papers:
        errors.append(f"{where}: proposal {pid} evidence[{index}] needs at least one paper")
    else:
        for paper in papers:
            errors.extend(_validate_paper(paper, paper_ids, where, pid))
    return errors


def _validate_proposals(
    push: dict, props: dict[str, dict], cards: dict[str, dict]
) -> list[str]:
    """Validate the v2 proposal contract and the 2-entity-target limit."""
    errors: list[str] = []
    where = f"push[{push.get('push_id')}]"
    paper_ids = push.get("paper_ids") or []
    proposals = push.get("proposals") or []
    targets: list[str] = []
    proposal_ids: set[str] = set()

    for proposal in proposals:
        pid = proposal.get("proposal_id") or "<missing>"
        op = proposal.get("op")
        target_type = proposal.get("target_type")
        target_id = proposal.get("target_id")

        if not PROPOSAL_ID_RE.match(str(pid)):
            errors.append(f"{where}: bad proposal_id {pid!r} (want p_<slug>)")
        if pid in proposal_ids:
            errors.append(f"{where}: duplicate proposal_id {pid}")
        proposal_ids.add(pid)
        if proposal.get("push_id") != push.get("push_id"):
            errors.append(f"{where}: proposal {pid} push_id mismatch")
        if proposal.get("status") != "draft":
            errors.append(f"{where}: proposal {pid} status must be draft")
        if proposal.get("schema_version") != 2:
            errors.append(f"{where}: proposal {pid} schema_version must be 2")
        if op not in PROPOSAL_KINDS:
            errors.append(f"{where}: proposal {pid} unknown op {op!r}")
            continue
        confidence = proposal.get("confidence")
        if not isinstance(confidence, (int, float)) or not (0 <= float(confidence) <= 1):
            errors.append(f"{where}: proposal {pid} confidence must be 0..1")
        rationale = proposal.get("rationale")
        if not isinstance(rationale, str) or len(rationale.strip()) < 8:
            errors.append(f"{where}: proposal {pid} needs a rationale")
        if op not in ("add_proposition_evidence", "merge_proposition_evidence"):
            errors.extend(_validate_evidence(proposal.get("evidence"), paper_ids, where, pid))

        diff = proposal.get("diff") or {}
        value = diff.get("value")
        before = proposal.get("before")
        after = proposal.get("after")

        if op in CARD_OPS:
            if op == "create_card":
                if target_type not in ("none", None) or target_id not in (None, ""):
                    errors.append(f"{where}: proposal {pid} create_card must not have a target")
                if not isinstance(value, dict):
                    errors.append(f"{where}: proposal {pid} create_card needs diff.value object")
                    continue
                card_id = value.get("id")
                label = value.get("label")
                ontology = value.get("ontology")
                annotation = value.get("annotation")
                if not label or not isinstance(label, str):
                    errors.append(f"{where}: proposal {pid} create_card needs label")
                if card_id and not CARD_ID_RE.match(card_id):
                    errors.append(f"{where}: proposal {pid} create_card id must look like card_<slug>")
                if card_id and card_id in cards:
                    errors.append(f"{where}: proposal {pid} create_card id {card_id} already exists")
                if not isinstance(ontology, list) or not ontology or not all(
                    isinstance(t, str) and t.strip() for t in ontology
                ):
                    errors.append(f"{where}: proposal {pid} create_card needs a non-empty ontology list")
                if not isinstance(annotation, str) or not annotation.strip():
                    errors.append(f"{where}: proposal {pid} create_card needs annotation")
                key = card_id or f"new:{label}"
                targets.append(f"card:{key}")
                if before is not None:
                    errors.append(f"{where}: proposal {pid} create_card before must be null")
            else:
                if target_type != "card" or target_id not in cards:
                    errors.append(f"{where}: proposal {pid} {op} targets unknown card {target_id}")
                    continue
                card = cards[target_id]
                targets.append(f"card:{target_id}")
                if op == "add_card_ontology":
                    terms = value if isinstance(value, list) else [value]
                    if not all(isinstance(t, str) and t.strip() for t in terms):
                        errors.append(f"{where}: proposal {pid} ontology terms must be strings")
                    current = list(card.get("ontology", []))
                    if before != current:
                        errors.append(f"{where}: proposal {pid} before does not match card ontology")
                    expected = current + [
                        t for t in terms if t.strip().lower() not in {x.lower() for x in current}
                    ]
                    if after != expected:
                        errors.append(f"{where}: proposal {pid} after must be before + new terms")
                elif op == "update_card_annotation":
                    if not isinstance(value, str) or len(value.strip()) < 20:
                        errors.append(f"{where}: proposal {pid} annotation must be a useful string")
                    if before != card.get("annotation", ""):
                        errors.append(f"{where}: proposal {pid} before does not match card annotation")
                    if after != value or after == before:
                        errors.append(f"{where}: proposal {pid} after must equal diff.value and change")
        else:
            if op == "create_proposition":
                if target_type not in ("none", None) or target_id not in (None, ""):
                    errors.append(f"{where}: proposal {pid} create_proposition must not have a target")
                if not isinstance(value, dict):
                    errors.append(f"{where}: proposal {pid} create_proposition needs diff.value object")
                    continue
                title = value.get("title")
                review = value.get("review")
                related = value.get("related_knowledge")
                evidence = value.get("evidence")
                prop_id = value.get("id")
                if not isinstance(title, str) or not title.strip():
                    errors.append(f"{where}: proposal {pid} create_proposition needs title")
                if not isinstance(review, str) or not (20 <= len(review.strip()) <= 2000):
                    errors.append(f"{where}: proposal {pid} create_proposition needs review (20..2000 chars)")
                if not isinstance(related, list) or len(set(related)) < 2:
                    errors.append(
                        f"{where}: proposal {pid} create_proposition needs >=2 related_knowledge cards"
                    )
                else:
                    for card_id in related:
                        if card_id not in cards:
                            errors.append(
                                f"{where}: proposal {pid} references unknown card {card_id} "
                                "(new cards cannot be linked in the same push)"
                            )
                if not isinstance(evidence, list) or len(evidence) < 2:
                    errors.append(f"{where}: proposal {pid} create_proposition needs >=2 evidence entries")
                else:
                    for index, entry in enumerate(evidence, start=1):
                        errors.extend(_validate_evidence_entry(entry, paper_ids, where, pid, index))
                if prop_id and not PROP_ID_RE.match(str(prop_id)):
                    errors.append(f"{where}: proposal {pid} proposition id must look like prop_<slug>")
                key = prop_id or f"new:{title}"
                targets.append(f"prop:{key}")
                if before is not None:
                    errors.append(f"{where}: proposal {pid} create_proposition before must be null")
            else:
                if target_type != "proposition" or target_id not in props:
                    errors.append(f"{where}: proposal {pid} {op} targets unknown proposition {target_id}")
                    continue
                prop = props[target_id]
                targets.append(f"prop:{target_id}")
                if op == "update_proposition_title":
                    if before != prop.get("title", ""):
                        errors.append(f"{where}: proposal {pid} before does not match proposition title")
                    if not isinstance(value, str) or not value.strip() or after != value or after == before:
                        errors.append(f"{where}: proposal {pid} needs a changed title")
                elif op == "update_proposition_review":
                    if before != prop.get("review", ""):
                        errors.append(f"{where}: proposal {pid} before does not match proposition review")
                    if (
                        not isinstance(value, str)
                        or not (20 <= len(value.strip()) <= 2000)
                        or after != value
                        or after == before
                    ):
                        errors.append(f"{where}: proposal {pid} needs a changed review (20..2000 chars)")
                elif op == "update_proposition_related_knowledge":
                    current = list(prop.get("related_knowledge", []))
                    if before != current:
                        errors.append(f"{where}: proposal {pid} before does not match related_knowledge")
                    if not isinstance(value, list) or len(set(value)) < 2:
                        errors.append(f"{where}: proposal {pid} needs >=2 related_knowledge cards")
                    else:
                        for card_id in value:
                            if card_id not in cards:
                                errors.append(f"{where}: proposal {pid} references unknown card {card_id}")
                    if after != value or after == before:
                        errors.append(f"{where}: proposal {pid} must change related_knowledge")
                elif op == "add_proposition_evidence":
                    if not isinstance(value, dict):
                        errors.append(f"{where}: proposal {pid} needs diff.value evidence entry")
                    else:
                        errors.extend(
                            _validate_evidence_entry(
                                value, paper_ids, where, pid, _evidence_count(prop) + 1
                            )
                        )
                        evidence_id = value.get("evidence_id") or value.get("id")
                        if evidence_id and evidence_id in {
                            e.get("id") for e in prop.get("evidence", [])
                        }:
                            errors.append(f"{where}: proposal {pid} evidence id {evidence_id} already exists")
                    if before != _evidence_count(prop):
                        errors.append(f"{where}: proposal {pid} before must be current evidence count")
                    if after != _evidence_count(prop) + 1:
                        errors.append(f"{where}: proposal {pid} after must be before + 1")
                elif op == "merge_proposition_evidence":
                    if not isinstance(value, dict):
                        errors.append(f"{where}: proposal {pid} needs diff.value evidence entry")
                        continue
                    target_evidence = next(
                        (
                            e
                            for e in prop.get("evidence", [])
                            if e.get("id") == value.get("target_evidence_id")
                        ),
                        None,
                    )
                    if not target_evidence:
                        errors.append(
                            f"{where}: proposal {pid} targets unknown evidence "
                            f"{value.get('target_evidence_id')!r}"
                        )
                        continue
                    papers = value.get("papers")
                    if not isinstance(papers, list) or not papers:
                        errors.append(f"{where}: proposal {pid} needs papers to merge")
                        papers = []
                    new_ids = set()
                    for paper in papers:
                        errors.extend(_validate_paper(paper, paper_ids, where, pid))
                        new_ids.add(paper.get("paper_id"))
                    existing = {p.get("paper_id") for p in target_evidence.get("papers", [])}
                    added = len(new_ids - existing)
                    conclusion = value.get("conclusion")
                    if conclusion is not None and (
                        not isinstance(conclusion, str)
                        or not (1 <= len(conclusion.strip()) <= 200)
                    ):
                        errors.append(f"{where}: proposal {pid} merged conclusion must be 1..200 chars")
                    if not added and conclusion is None:
                        errors.append(f"{where}: proposal {pid} merge must add a paper or a conclusion")
                    if before != len(target_evidence.get("papers", [])):
                        errors.append(f"{where}: proposal {pid} before must be current paper count")
                    if after != before + added:
                        errors.append(f"{where}: proposal {pid} after must be before + new papers")

    if len(dict.fromkeys(targets)) > MAX_TARGETS:
        errors.append(
            f"{where}: {len(dict.fromkeys(targets))} entity targets > limit {MAX_TARGETS}"
        )
    return errors


def validate(task: dict, outbox: Path) -> dict:
    errors: list[str] = []
    pushes_dir = Path(outbox) / "pushes"
    files = sorted(pushes_dir.glob("*.json")) if pushes_dir.exists() else []
    if not files:
        return {"ok": False, "errors": [f"no push files under {pushes_dir}"]}

    expected = {
        item["paper"]["paper_id"]: item for item in task.get("items", []) if item.get("paper")
    }
    props, cards = _target_sets(task)
    seen_papers: dict[str, str] = {}
    proposal_ids: set[str] = set()
    date = task.get("date")

    for path in files:
        push = read_json(path) or {}
        name = f"{path.name}"
        where = f"push[{name}]"
        for key in ("push_id", "date", "checkpoint_id", "paper_ids", "sections", "proposals"):
            if key not in push:
                errors.append(f"{where}: missing {key}")
        if "push_id" in push and path.stem != push["push_id"]:
            errors.append(f"{where}: filename must equal push_id")
        if push.get("date") != date:
            errors.append(f"{where}: date {push.get('date')} != task date {date}")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}_[a-z0-9_-]+_\d{2}", str(push.get("push_id", ""))):
            errors.append(f"{where}: push_id must look like <date>_<checkpoint>_<NN>")

        paper_ids = push.get("paper_ids") or []
        if not paper_ids:
            errors.append(f"{where}: paper_ids is empty")
        for pid in paper_ids:
            if pid not in expected:
                errors.append(f"{where}: paper {pid} is not in the task")
            elif pid in seen_papers:
                errors.append(f"{where}: paper {pid} already used by {seen_papers[pid]}")
            else:
                seen_papers[pid] = name

        if push.get("format") == "pdf":
            errors.extend(f"{where}: {error}" for error in inspect_pdf(push, path.parent))
        else:
            sections = push.get("sections") or {}
            for key in ("did", "found", "why", "keep"):
                value = sections.get(key)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{where}: sections.{key} must be non-empty")
            if not isinstance(push.get("body_md"), str) or not push["body_md"].strip():
                errors.append(f"{where}: body_md must be non-empty")

        errors.extend(_validate_proposals(push, props, cards))
        for proposal in push.get("proposals") or []:
            proposal_ids.add(proposal.get("proposal_id"))

    missing = set(expected) - set(seen_papers)
    for pid in sorted(missing):
        errors.append(f"missing push for paper {pid}")

    return {
        "ok": not errors,
        "errors": errors,
        "pushes": len(files),
        "expected": len(expected),
        "proposals": len(proposal_ids),
    }
