"""Progress recorded against a persistent Frontier."""

import json
import time
import uuid

from hostrun.agent_service import CommandError
from hostrun.library_tags import new_test_tags


def initialize(db):
    db.execute("""CREATE TABLE IF NOT EXISTS frontier_progress(
        progress_id TEXT PRIMARY KEY, frontier_id TEXT NOT NULL, result TEXT NOT NULL)""")
    db.execute("""CREATE INDEX IF NOT EXISTS frontier_progress_parent
        ON frontier_progress(frontier_id)""")


def list_for_frontier(db, frontier_id):
    rows = db.execute("""SELECT result FROM frontier_progress WHERE frontier_id=?
        AND json_extract(result,'$.status')='active' ORDER BY rowid DESC""",
                      (frontier_id,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def apply(db, action, command, *, frontier, check_reference, now=None):
    allowed = {"schema_version", "operation_id", "frontier_id"}
    if action == "create":
        allowed.add("test_data")
    if action != "create":
        allowed.update(("progress_id", "expected_version"))
    if action != "delete":
        allowed.update(("content", "source_ids"))
    if set(command) - allowed or (
        "schema_version" in command
        and (type(command["schema_version"]) is not int or command["schema_version"] != 1)
    ):
        raise CommandError("invalid_command", "前沿进展包含未知字段或版本")
    frontier_id = frontier["frontier_id"]
    if frontier["status"] == "deleted":
        raise CommandError("frontier_inactive", "已删除的前沿不能修改进展")
    if now is None:
        now = time.time()
    if action == "create":
        progress = {"progress_id": str(uuid.uuid4()), "frontier_id": frontier_id,
                    "version": 0, "status": "active", "created_at": int(now), **new_test_tags(command)}
    else:
        progress_id = command.get("progress_id")
        if not isinstance(progress_id, str):
            raise CommandError("invalid_command", "需要进展标识")
        row = db.execute("SELECT result FROM frontier_progress WHERE frontier_id=? AND progress_id=?",
                         (frontier_id, progress_id)).fetchone()
        if not row:
            raise CommandError("not_found", "未找到前沿进展")
        progress = json.loads(row[0])
        if progress["status"] != "active":
            raise CommandError("not_found", "未找到前沿进展")
        if type(command.get("expected_version")) is not int or command["expected_version"] != progress["version"]:
            raise CommandError("version_conflict", "前沿进展已被修改，请刷新后重试")

    if action == "delete":
        progress["status"] = "deleted"
    else:
        content = command.get("content", progress.get("content"))
        if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 12000:
            raise CommandError("invalid_command", "进展须描述一项可独立理解的变化（最多 12000 字节）")
        source_ids = command.get("source_ids", progress.get("source_ids", []))
        if not isinstance(source_ids, list) or len(source_ids) > 32 or any(
            not isinstance(source_id, str) or not source_id for source_id in source_ids
        ):
            raise CommandError("invalid_command", "进展最多可关联 32 个资料库条目")
        source_ids = list(dict.fromkeys(source_ids))
        for source_id in source_ids:
            check_reference(source_id, source_id in progress.get("source_ids", []))
        progress.update(content=content.strip(), source_ids=source_ids)
    progress.update(version=progress["version"] + 1, updated_at=int(now))
    db.execute("""INSERT INTO frontier_progress VALUES(?,?,?)
        ON CONFLICT(progress_id) DO UPDATE SET result=excluded.result""",
               (progress["progress_id"], frontier_id, json.dumps(progress, ensure_ascii=False)))
    return progress
