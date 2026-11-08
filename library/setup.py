"""Create the 原文 / 推送日志 / 决定 / 配置 tables and the two Drive folders.

Idempotent: existing tables, fields and folders are reused. Never deletes or
renames user data. Run with ``--dry-run`` first.
"""

from __future__ import annotations

from pathlib import Path

from common import now
from library import base as base_mod
from library import config as config_mod
from library.drive import LarkDrive

SOURCE_TABLE = "原文"
PUSH_TABLE = "推送日志"
DECISION_TABLE = "决定"
CONFIG_TABLE = "配置"

SOURCE_FIELDS = [
    {"name": "原文 ID", "type": "text"},
    {"name": "标题", "type": "text"},
    {"name": "作者", "type": "text"},
    {"name": "期刊", "type": "text"},
    {"name": "发表年份", "type": "number"},
    {"name": "DOI", "type": "text"},
    {"name": "PMID", "type": "text"},
    {"name": "PMCID", "type": "text"},
    {"name": "arXiv", "type": "text"},
    {"name": "摘要", "type": "text"},
    {"name": "分类", "type": "text"},
    {"name": "是否预印本", "type": "checkbox"},
    {"name": "PDF 页数", "type": "number"},
    {
        "name": "来源",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "pdf"},
            {"name": "arxiv"},
            {"name": "europepmc"},
            {"name": "pubmed"},
            {"name": "fixture"},
        ],
    },
    {"name": "原文来源链接", "type": "url"},
    {"name": "入库日期", "type": "datetime"},
    {"name": "云盘链接", "type": "url"},
    {"name": "文件大小", "type": "number"},
    {"name": "md5", "type": "text"},
    {"name": "分片", "type": "text"},
    {
        "name": "状态",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "待上传"},
            {"name": "在库"},
            {"name": "无原件"},
            {"name": "已离线归档"},
        ],
    },
    {"name": "备份位置", "type": "text"},
]

PUSH_FIELDS = [
    {"name": "推送 ID", "type": "text"},
    {"name": "日期", "type": "datetime"},
    {"name": "标题", "type": "text"},
    {"name": "一句话结论", "type": "text"},
    {"name": "背景", "type": "text"},
    {"name": "方法", "type": "text"},
    {"name": "结果", "type": "text"},
    {"name": "与知识的关系", "type": "text"},
    {"name": "局限", "type": "text"},
    {"name": "来源", "type": "text"},
    {
        "name": "评分",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "irrelevant"},
            {"name": "ok"},
            {"name": "great"},
        ],
    },
    {"name": "评语", "type": "text"},
    {"name": "云盘链接", "type": "url"},
    {"name": "文件大小", "type": "number"},
    {"name": "md5", "type": "text"},
    {"name": "入库日期", "type": "datetime"},
    {
        "name": "状态",
        "type": "select",
        "multiple": False,
        "options": [
            {"name": "在库"},
            {"name": "已离线归档"},
            {"name": "回填"},
        ],
    },
]

DECISION_FIELDS = [
    {"name": "决定 ID", "type": "text"},
    {"name": "问题 ID", "type": "text"},
    {"name": "时间", "type": "datetime"},
    {"name": "提议 ID", "type": "text"},
    {
        "name": "操作",
        "type": "select",
        "multiple": False,
        "options": [{"name": "采纳"}, {"name": "拒绝"}],
    },
    {"name": "目标", "type": "text"},
    {"name": "评语", "type": "text"},
]

CONFIG_FIELDS = [
    {"name": "键", "type": "text"},
    {"name": "容量上限（GB）", "type": "number"},
    {"name": "提醒阈值（%）", "type": "number"},
    {"name": "原文目录 token", "type": "text"},
    {"name": "推送目录 token", "type": "text"},
    {"name": "最近对账", "type": "datetime"},
]

LINK_FIELDS = {
    PUSH_TABLE: [
        {"name": "知识卡", "type": "link", "link_table": "cards", "bidirectional": False},
        {"name": "原文", "type": "link", "link_table": SOURCE_TABLE, "bidirectional": False},
    ],
    DECISION_TABLE: [
        {"name": "推送", "type": "link", "link_table": PUSH_TABLE, "bidirectional": False},
    ],
}


def _ensure_table(client, existing: dict[str, str], name: str, fields: list[dict], *, dry_run: bool) -> tuple[str, list[str]]:
    actions = []
    if name in existing:
        return existing[name], actions
    actions.append(f"create table {name}")
    if dry_run:
        return f"tbl_dry_{name}", actions
    table_id = client.create_table(name, fields)
    existing[name] = table_id
    return table_id, actions


def _ensure_fields(client, table_id: str, fields: list[dict], *, dry_run: bool) -> list[str]:
    existing = (
        client.field_names(table_id) if not table_id.startswith("tbl_dry_") else []
    )
    missing = [f for f in fields if f["name"] not in existing]
    if not missing:
        return []
    actions = [f"add fields to {table_id}: {[f['name'] for f in missing]}"]
    if not dry_run:
        client.create_fields(table_id, missing)
    return actions


def setup(
    home: Path,
    *,
    dry_run: bool = False,
    base_token: str | None = None,
    identity: str = "user",
    client=None,
    drive=None,
    names: dict | None = None,
) -> dict:
    home = Path(home)
    cfg = base_mod.load_base_config(home)
    if base_token:
        cfg["base_token"] = base_token
    names = names or {}
    source_table = str(names.get("source") or SOURCE_TABLE)
    push_table = str(names.get("push") or PUSH_TABLE)
    decision_table = str(names.get("decision") or DECISION_TABLE)
    config_table = str(names.get("config") or CONFIG_TABLE)
    root_folder_name = str(names.get("root_folder") or "horizon")
    source_folder_name = str(names.get("source_folder") or "原文")
    push_folder_name = str(names.get("push_folder") or "推送")
    client = client or base_mod.LarkBaseClient(cfg, identity=identity)
    drive = drive or LarkDrive(identity=identity)

    listing = client.list_tables()
    existing = {t["name"]: t["id"] for t in listing}
    names_by_id = {t["id"]: t["name"] for t in listing}
    tables = dict(cfg["tables"])
    actions: list[str] = []

    source_id, acts = _ensure_table(client, existing, source_table, SOURCE_FIELDS, dry_run=dry_run)
    actions += acts
    actions += _ensure_fields(client, source_id, SOURCE_FIELDS, dry_run=dry_run)
    tables[base_mod.SOURCE] = source_id

    push_id, acts = _ensure_table(client, existing, push_table, PUSH_FIELDS, dry_run=dry_run)
    actions += acts
    actions += _ensure_fields(client, push_id, PUSH_FIELDS, dry_run=dry_run)
    tables[base_mod.PUSH] = push_id

    decision_id, acts = _ensure_table(client, existing, decision_table, DECISION_FIELDS, dry_run=dry_run)
    actions += acts
    actions += _ensure_fields(client, decision_id, DECISION_FIELDS, dry_run=dry_run)
    tables[base_mod.DECISION] = decision_id

    config_id, acts = _ensure_table(client, existing, config_table, CONFIG_FIELDS, dry_run=dry_run)
    actions += acts
    actions += _ensure_fields(client, config_id, CONFIG_FIELDS, dry_run=dry_run)
    tables[base_mod.CONFIG] = config_id

    # the old per-quote table is 「报道」, not 「papers」 (id never changes)
    papers_id = tables.get("papers")
    if papers_id and not papers_id.startswith("tbl_dry_"):
        current_name = names_by_id.get(papers_id)
        if current_name and current_name != "报道" and not names:
            actions.append(f"rename {papers_id} {current_name} -> 报道")
            if not dry_run:
                client.rename_table(papers_id, "报道")

    # links (created after both sides exist)
    cards_id = tables.get("cards")
    if cards_id and not push_id.startswith("tbl_dry_"):
        for spec in LINK_FIELDS[PUSH_TABLE]:
            target = cards_id if spec["link_table"] == "cards" else source_id
            field = {**spec, "link_table": target}
            actions += _ensure_fields(client, push_id, [field], dry_run=dry_run)
        for spec in LINK_FIELDS[DECISION_TABLE]:
            field = {**spec, "link_table": push_id}
            actions += _ensure_fields(client, decision_id, [field], dry_run=dry_run)

    # reverse link on 报道 → 原文
    if papers_id and not source_id.startswith("tbl_dry_"):
        actions += _ensure_fields(
            client,
            papers_id,
            [{"name": "原文", "type": "link", "link_table": source_id,
              "bidirectional": True}],
            dry_run=dry_run,
        )

    # Drive folders
    root = getattr(drive, "root", "")
    source_folder = push_folder = ""
    if dry_run:
        actions.append(f"ensure folders {root_folder_name}/{source_folder_name} and {root_folder_name}/{push_folder_name}")
    else:
        horizon = drive.ensure_folder(root, root_folder_name)
        source_folder = drive.ensure_folder(horizon, source_folder_name)
        push_folder = drive.ensure_folder(horizon, push_folder_name)
        actions.append(f"folders: {source_folder} / {push_folder}")

    # config row (create once; never overwrite a user-set limit)
    if not config_id.startswith("tbl_dry_"):
        rows = client.list(config_id)
        row = next((r for r in rows if str(r["fields"].get("键") or "") == "config"), None)
        if row is None:
            if not dry_run:
                client.create(
                    config_id,
                    [{
                        "键": "config",
                        "容量上限（GB）": config_mod.DEFAULT_LIMIT_GB,
                        "提醒阈值（%）": config_mod.DEFAULT_THRESHOLD,
                        "原文目录 token": source_folder,
                        "推送目录 token": push_folder,
                        "最近对账": now(),
                    }],
                )
            actions.append("create 配置 row")
        elif not dry_run:
            patch = {}
            if not row["fields"].get("原文目录 token"):
                patch["原文目录 token"] = source_folder
            if not row["fields"].get("推送目录 token"):
                patch["推送目录 token"] = push_folder
            if patch:
                client.update(config_id, {row["record_id"]: patch})
                actions.append("fill folder tokens")

    if not dry_run:
        cfg["tables"] = {**cfg["tables"], **tables}
        base_mod.save_base_config(home, cfg)
        config_mod.invalidate(home)

    return {
        "ok": True,
        "dry_run": dry_run,
        "base_token": cfg["base_token"],
        "tables": tables,
        "source_folder": source_folder,
        "push_folder": push_folder,
        "actions": actions,
    }
