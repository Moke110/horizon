"""Review task: write a proposition review (four sections) from its evidence.

The agent writes ``outbox/reviews/<prop_id>.json``; this module builds the task,
validates the result, and renders a self-contained HTML file. It never writes
archive.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from archive.store import Archive
from common import now
from writer.render import FORBIDDEN_TAG_RE, EVENT_ATTR_RE, JS_URL_RE, WEB_STYLE

SECTIONS = ("结论", "支持证据", "反证与边界", "待验证")
MAX_REVIEW = 2000
REVIEW_ID_RE = re.compile(r"^p_[a-z0-9_-]+$")

INSTRUCTIONS = """你是知识循环的综述写作者。基于命题当前的全部 evidence，重写这条命题的 review。
要求：
1) review 固定四节：## 结论 / ## 支持证据 / ## 反证与边界 / ## 待验证；每节 1–3 段，总长 ≤2000 字；
2) 只使用 task.evidence 里的事实，不得引入新论文或新数据；
3) 支持与反证必须分开写；有矛盾就明确写出矛盾，不要调和；
4) body_html 是 review 的 HTML 片段，用 <h2>/<p>/<ul>/<blockquote> 组织；
   相对上一版的改动用 <span class="diff-del">…</span> / <span class="diff-add">…</span> 包裹；
5) proposal 的 op=update_proposition_review，before=task.previous_review，after=review；
6) 只输出一个 JSON 文件到 outbox/reviews/<prop_id>.json，不修改 archive。"""


def build_task(home: Path, prop_id: str, item_id: str | None = None) -> dict:
    store = Archive(Path(home))
    prop = store.get_prop(prop_id)
    evidence = prop.get("evidence") or []
    return {
        "schema_version": 1,
        "kind": "review-task",
        "prop_id": prop_id,
        "item_id": item_id,
        "generated_at": now(),
        "title": prop.get("title", ""),
        "previous_review": prop.get("review", ""),
        "related_knowledge": prop.get("related_knowledge", []),
        "evidence": evidence,
        "stats": {
            "evidence_count": len(evidence),
            "supports": sum(1 for e in evidence if e.get("stance") == "supports"),
            "contradicts": sum(1 for e in evidence if e.get("stance") == "contradicts"),
            "evidence_updates_since_review": prop.get(
                "evidence_updates_since_review", 0
            ),
        },
        "instructions": INSTRUCTIONS,
        "output": {
            "dir": "outbox/reviews",
            "filename": f"{prop_id}.json",
        },
    }


def _sections(review: str) -> list[str]:
    return [part.strip() for part in re.split(r"^##\s+", review, flags=re.M) if part.strip()]


def _line_diff(before: str, after: str) -> dict:
    old = [line for line in before.splitlines() if line.strip()]
    new = [line for line in after.splitlines() if line.strip()]
    added = sum(1 for line in new if line not in old)
    removed = sum(1 for line in old if line not in new)
    return {"added": added, "removed": removed}


def validate(task: dict, review: dict) -> list[str]:
    errors: list[str] = []
    if review.get("prop_id") != task.get("prop_id"):
        errors.append("review.prop_id must match task.prop_id")
    text = review.get("review")
    if not isinstance(text, str) or not text.strip():
        errors.append("review.review must be non-empty")
    else:
        if len(text) > MAX_REVIEW:
            errors.append(f"review.review exceeds {MAX_REVIEW} chars")
        headings = [part.splitlines()[0].strip() for part in _sections(text)]
        if headings != list(SECTIONS):
            errors.append(
                "review.review must have exactly four sections: "
                + " / ".join(SECTIONS)
            )
    body = review.get("body_html")
    if not isinstance(body, str) or not body.strip():
        errors.append("review.body_html must be non-empty")
    else:
        for match in FORBIDDEN_TAG_RE.finditer(body):
            errors.append(f"body_html contains forbidden tag <{match.group(1)}>")
        if EVENT_ATTR_RE.search(body):
            errors.append("body_html must not contain inline event handlers")
        if JS_URL_RE.search(body):
            errors.append("body_html must not contain javascript: URLs")

    proposal = review.get("proposal") or {}
    if proposal.get("op") != "update_proposition_review":
        errors.append("proposal.op must be update_proposition_review")
    if proposal.get("target_id") != task.get("prop_id"):
        errors.append("proposal.target_id must match task.prop_id")
    if proposal.get("before") != task.get("previous_review"):
        errors.append("proposal.before must equal task.previous_review")
    if proposal.get("after") != text:
        errors.append("proposal.after must equal review.review")
    if (proposal.get("diff") or {}).get("value") != text:
        errors.append("proposal.diff.value must equal review.review")
    if not REVIEW_ID_RE.match(str(proposal.get("proposal_id") or "")):
        errors.append("proposal.proposal_id must look like p_<slug>")
    if proposal.get("status") != "draft":
        errors.append("proposal.status must be draft")
    if len(str(proposal.get("rationale") or "").strip()) < 8:
        errors.append("proposal needs a rationale")
    confidence = proposal.get("confidence")
    if not isinstance(confidence, (int, float)) or not (0 <= float(confidence) <= 1):
        errors.append("proposal.confidence must be 0..1")

    review["stats"] = {
        **(review.get("stats") or {}),
        **_line_diff(task.get("previous_review", ""), text or ""),
    }
    return errors


def _evidence_html(evidence: list[dict]) -> str:
    if not evidence:
        return "<p>暂无证据。</p>"
    parts = []
    for stance, label in (("supports", "支持"), ("contradicts", "反证")):
        rows = [e for e in evidence if e.get("stance") == stance]
        if not rows:
            continue
        parts.append(f"<h2>{label}（{len(rows)} 条）</h2>")
        for entry in rows:
            parts.append(f"<h3>{html.escape(entry.get('id', ''))}</h3>")
            parts.append(f"<p>{html.escape(entry.get('conclusion', ''))}</p>")
            for paper in entry.get("papers") or []:
                page = f"第 {paper.get('page')} 页" if paper.get("page") else "摘要"
                parts.append(
                    "<details><summary>"
                    f"{html.escape(paper.get('paper_id', ''))} · {page}"
                    "</summary>"
                    f"<blockquote>{html.escape(paper.get('quote', ''))}</blockquote>"
                    "</details>"
                )
    return "\n".join(parts)


def render_html(home: Path, review: dict, out_path: Path) -> Path:
    store = Archive(Path(home))
    prop = store.get_prop(review["prop_id"])
    evidence = prop.get("evidence") or []
    supports = sum(1 for e in evidence if e.get("stance") == "supports")
    contradicts = len(evidence) - supports
    css = WEB_STYLE.read_text(encoding="utf-8")
    body = review.get("body_html") or ""
    document = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(prop.get('title', review['prop_id']))}</title>
<style>{css}</style>
</head>
<body>
<header class="masthead">
  <div class="kicker">HORIZON · 命题综述</div>
  <h1 class="title">{html.escape(prop.get('title', ''))}</h1>
  <p class="dek">{html.escape(review['prop_id'])} · 基于 {len(evidence)} 条证据（支持 {supports} / 反证 {contradicts}）</p>
</header>
<main class="article">
{body}
<h2>证据附录</h2>
{_evidence_html(evidence)}
</main>
<footer class="colophon">
  <span>{html.escape(review['prop_id'])} · {html.escape(str(review.get('generated_at') or now())[:10])}</span>
  <span>horizon · 命题综述</span>
</footer>
</body>
</html>
"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(document, encoding="utf-8")
    return out_path
