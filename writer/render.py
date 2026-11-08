"""Render a long-form push (HTML fragment + assets) into a PDF.

The agent writes only the article fragment; the layout, fonts, page geometry and
figure captions live in ``templates/`` and are applied here. Figure placeholders
(``<figure data-asset="fig-1"></figure>``) are expanded with local assets so the
agent never has to know the final file path or caption markup.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path

from common import clip, read_json

SECTIONS = (
    "takeaway",
    "background",
    "methods",
    "results",
    "why",
    "limits",
    "sources",
)

SECTION_LABELS = {
    "takeaway": "一句话结论",
    "background": "研究背景与问题",
    "methods": "方法",
    "results": "关键结果",
    "why": "为什么重要",
    "limits": "局限与存疑",
    "sources": "原文与数据",
}

# Rendered caption pills: the source of truth a rendered push must contain.
CAPTION_LABELS = {
    "paper": "原文图",
    "generated": "生成图",
}

FIGURE_RE = re.compile(r'<figure\s+data-asset="([^"]+)"\s*>\s*</figure>', re.IGNORECASE)
FORBIDDEN_TAG_RE = re.compile(
    r"<\s*(script|style|link|iframe|object|embed|form|input|button)\b", re.IGNORECASE
)
EVENT_ATTR_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
JS_URL_RE = re.compile(r"javascript\s*:", re.IGNORECASE)

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "templates" / "push.html"
STYLE = HERE / "templates" / "push.css"
WEB_STYLE = HERE / "templates" / "push-web.css"

SOURCE_LABEL = {
    "arxiv": "arXiv",
    "europepmc": "Europe PMC",
    "fixture": "Fixture",
    "pubmed": "PubMed",
}


class RenderError(RuntimeError):
    pass


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)
    return str(value)


def _asset_map(push: dict) -> tuple[dict[str, dict], list[str]]:
    errors: list[str] = []
    assets: dict[str, dict] = {}
    for index, asset in enumerate(push.get("assets") or [], start=1):
        if not isinstance(asset, dict):
            errors.append(f"assets[{index}] must be an object")
            continue
        aid = asset.get("id")
        if not aid:
            errors.append(f"assets[{index}] missing id")
            continue
        if aid in assets:
            errors.append(f"asset id {aid} is duplicated")
            continue
        if not asset.get("path"):
            errors.append(f"asset {aid} missing path")
        if not (asset.get("caption") or "").strip():
            errors.append(f"asset {aid} missing caption")
        if asset.get("source") not in ("paper", "generated"):
            errors.append(f"asset {aid} source must be paper|generated")
        assets[aid] = asset
    return assets, errors


def _resolve_asset(path: str, base_dir: Path) -> Path | None:
    raw = Path(path)
    if raw.is_absolute() or ".." in raw.parts:
        return None
    resolved = (base_dir / raw).resolve()
    try:
        resolved.relative_to(base_dir.resolve())
    except ValueError:
        return None
    return resolved


def inspect(push: dict, base_dir: Path) -> list[str]:
    """PDF-specific structural checks. Proposals are validated by writer.tasks."""
    errors: list[str] = []
    base_dir = Path(base_dir)

    sections = push.get("sections") or {}
    for key in SECTIONS:
        value = sections.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"sections.{key} must be non-empty")
    sources = str(sections.get("sources") or "")
    if "http" not in sources.lower():
        errors.append("sections.sources must include the original URL")

    paper = push.get("paper") or {}
    paper_id = paper.get("paper_id")
    if not paper_id:
        errors.append("paper.paper_id is required")
    elif paper_id not in (push.get("paper_ids") or []):
        errors.append(f"paper {paper_id} is not in paper_ids")

    body = push.get("body_html")
    if not isinstance(body, str) or not body.strip():
        errors.append("body_html must be non-empty")
        body = ""
    for match in FORBIDDEN_TAG_RE.finditer(body):
        errors.append(f"body_html contains forbidden tag <{match.group(1)}>")
    if EVENT_ATTR_RE.search(body):
        errors.append("body_html must not contain inline event handlers")
    if JS_URL_RE.search(body):
        errors.append("body_html must not contain javascript: URLs")

    assets, asset_errors = _asset_map(push)
    errors.extend(asset_errors)

    placeholders = FIGURE_RE.findall(body)
    if not placeholders:
        errors.append("body_html must contain at least one <figure data-asset=\"...\"></figure>")
    for aid in placeholders:
        if aid not in assets:
            errors.append(f"figure references unknown asset {aid}")
    if len(set(placeholders)) < len(placeholders):
        errors.append("body_html reuses the same figure asset more than once")

    for aid, asset in assets.items():
        path = asset.get("path")
        if not path:
            continue
        resolved = _resolve_asset(path, base_dir)
        if resolved is None:
            errors.append(f"asset {aid} path must stay inside the outbox")
        elif not resolved.exists():
            errors.append(f"asset {aid} file not found: {path}")
        elif resolved.stat().st_size == 0:
            errors.append(f"asset {aid} file is empty: {path}")

    return errors


def _data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    mime = mime or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def expand_figures(
    fragment: str, assets: dict[str, dict], base_dir: Path, *, inline: bool = False
) -> str:
    def replace(match: re.Match) -> str:
        aid = match.group(1)
        asset = assets[aid]
        path = _resolve_asset(asset["path"], base_dir)
        if path is None or not path.exists():
            raise RenderError(f"asset {aid} not found: {asset.get('path')}")
        source = asset["source"]
        pill = CAPTION_LABELS["paper"] if source == "paper" else CAPTION_LABELS["generated"]
        pill_class = "source-pill" if source == "paper" else "source-pill generated"
        alt = html.escape((asset.get("caption") or aid)[:160], quote=True)
        src = _data_uri(path) if inline else path.as_uri()
        return (
            '<figure class="push-figure">'
            f'<img src="{src}" alt="{alt}">'
            "<figcaption>"
            f"{html.escape(asset['caption'], quote=False)}"
            f'<span class="{pill_class}">{pill}</span>'
            "</figcaption>"
            "</figure>"
        )

    return FIGURE_RE.sub(replace, fragment)


def _meta_rows(push: dict) -> str:
    paper = push.get("paper") or {}
    source = SOURCE_LABEL.get(str(paper.get("source") or push.get("source")), _text(paper.get("source") or push.get("source")))
    authors = paper.get("authors") or []
    if isinstance(authors, str):
        authors = [authors]
    if len(authors) > 6:
        authors = list(authors[:5]) + ["et al."]
    rows = [
        ("日期", _text(push.get("date"))),
        ("来源", source),
        ("期刊", _text(paper.get("journal"))),
        ("发表", _text(paper.get("published"))),
        ("作者", _text(authors) or "—"),
        ("论文", _text(paper.get("paper_id"))),
    ]
    return "\n".join(
        f"    <div><dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd></div>"
        for label, value in rows
    )


def build_html(push: dict, *, web: bool = False) -> str:
    base_dir = Path(push["_base_dir"])
    body = push.get("body_html") or ""
    assets, asset_errors = _asset_map(push)
    if asset_errors:
        raise RenderError("; ".join(asset_errors))
    body = expand_figures(body, assets, base_dir, inline=web)

    sections = push.get("sections") or {}
    paper = push.get("paper") or {}
    label = push.get("checkpoint_label") or push.get("checkpoint_id") or ""
    kicker = f"HORIZON · {label}"

    template = TEMPLATE.read_text(encoding="utf-8")
    css = (WEB_STYLE if web else STYLE).read_text(encoding="utf-8")
    replacements = {
        "{{CSS}}": css,
        "{{TITLE}}": html.escape(_text(push.get("title"))),
        "{{KICKER}}": html.escape(kicker),
        "{{DEK}}": html.escape(clip(_text(sections.get("takeaway")), 190)),
        "{{META}}": _meta_rows(push),
        "{{BODY}}": body,
        "{{FOOTER_LEFT}}": html.escape(f"{label} · {push.get('push_id', '')}"),
        "{{FOOTER_RIGHT}}": html.escape(f"生成于 {str(push.get('created_at', ''))[:10]}"),
    }
    for token, value in replacements.items():
        template = template.replace(token, value)
    _ = paper
    return template


def render_html(push_path: Path, out_path: Path | None = None) -> dict:
    """Render a self-contained responsive HTML file (inline CSS + base64 images)."""
    push_path = Path(push_path).resolve()
    push = read_json(push_path)
    if not push:
        raise RenderError(f"no push at {push_path}")
    push = {**push, "_base_dir": str(push_path.parent)}
    errors = inspect(push, push_path.parent)
    if errors:
        raise RenderError("; ".join(errors))

    out_path = Path(out_path) if out_path else push_path.with_suffix(".html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document_html = build_html(push, web=True)
    out_path.write_text(document_html, encoding="utf-8")
    return {
        "ok": True,
        "push_id": push.get("push_id"),
        "html": str(out_path),
        "bytes": out_path.stat().st_size,
        "figures": len(push.get("assets") or []),
    }


def render_push(
    push_path: Path, out_path: Path | None = None, *, fmt: str | None = None
) -> dict:
    """Render one push. ``fmt`` wins; otherwise infer from the output suffix.

    The CLI defaults to self-contained HTML; calling without either argument
    keeps the legacy PDF behaviour for older callers.
    """
    push_path = Path(push_path).resolve()
    if fmt == "html":
        return render_html(push_path, Path(out_path) if out_path else None)
    if fmt == "pdf":
        out_path = Path(out_path) if out_path else push_path.with_suffix(".pdf")
    elif out_path is not None and Path(out_path).suffix.lower() in (".html", ".htm"):
        return render_html(push_path, Path(out_path))
    push = read_json(push_path)
    if not push:
        raise RenderError(f"no push at {push_path}")
    push = {**push, "_base_dir": str(push_path.parent)}
    errors = inspect(push, push_path.parent)
    if errors:
        raise RenderError("; ".join(errors))

    out_path = Path(out_path) if out_path else push_path.with_suffix(".pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    document_html = build_html(push)

    try:
        from weasyprint import HTML
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RenderError("weasyprint is not installed") from exc

    doc = HTML(string=document_html, base_url=str(push_path.parent)).render()
    doc.write_pdf(str(out_path))
    return {
        "ok": True,
        "push_id": push.get("push_id"),
        "pdf": str(out_path),
        "pages": len(doc.pages),
        "bytes": out_path.stat().st_size,
        "figures": len(push.get("assets") or []),
    }
