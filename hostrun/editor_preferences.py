"""Versioned writing style choices and opt-in feedback candidates."""

import hashlib
import hmac
import json
import re
import uuid

from hostrun.agent_service import CommandError
from hostrun.frontier import FrontierService
from hostrun.managed_database import database_connection


OPTIONS = {
    "length": ("standard", "concise", "detailed"),
    "structure": ("standard", "sections", "conclusion_first"),
}
_INFER = (
    ("length", "concise", re.compile(r"太啰嗦|太冗长|篇幅太长|更简洁|too (?:long|verbose)", re.I)),
    ("length", "detailed", re.compile(r"太简略|需要更多细节|展开一些|more detail", re.I)),
    ("structure", "conclusion_first", re.compile(r"结论前置|先讲结论|conclusion first", re.I)),
)


def _validate(key, value):
    if not isinstance(key, str) or key not in OPTIONS or value not in OPTIONS[key]:
        raise CommandError("invalid_preference", "请选择支持的写作样式")
    return value


def _schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS editor_writing_preferences(
        scope TEXT NOT NULL, name TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(scope,name))""")
    db.execute("""CREATE TABLE IF NOT EXISTS editor_writing_candidates(
        candidate_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, report_version INTEGER NOT NULL,
        task_id TEXT NOT NULL, result TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS editor_writing_operations(
        operation_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL, result TEXT NOT NULL)""")


def record_feedback_candidate(db, report_id, version, task_id, feedback):
    """Reconcile unadopted feedback suggestions on the report owner's transaction."""
    _schema(db)
    for cid, encoded in db.execute("SELECT candidate_id,result FROM editor_writing_candidates "
                                   "WHERE report_id=? AND report_version=? AND task_id=?",
                                   (report_id, version, task_id)).fetchall():
        row = json.loads(encoded)
        if row["status"] == "candidate":
            row["status"] = "superseded"
            db.execute("UPDATE editor_writing_candidates SET result=? WHERE candidate_id=?",
                       (json.dumps(row), cid))
    reason = feedback.get("reason") or ""
    if feedback["score"] is None or not reason:
        return
    hits = {pref: [value for key, value, pattern in _INFER
                   if key == pref and pattern.search(reason)] for pref in OPTIONS}
    for key, values in hits.items():
        if len(values) != 1:
            continue
        identity = str(uuid.uuid4())
        source = {"kind": "feedback", "report_id": report_id, "report_version": version,
                  "feedback_revision": feedback["revision"], "score": feedback["score"]}
        row = {"id": identity, "version": 1, "key": key, "value": values[0],
               "status": "candidate", "source": source}
        db.execute("INSERT INTO editor_writing_candidates VALUES(?,?,?,?,?)",
                   (identity, report_id, version, task_id, json.dumps(row)))


class PreferenceService:
    def __init__(self, home, *, token):
        if not isinstance(token, str) or not token:
            raise ValueError("Launch token required")
        self.home, self.token = home, token
        with database_connection(home) as db, db:
            _schema(db)

    def _authorize(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
            raise PermissionError("Unauthorized Editor preference access")

    def list(self, *, token, task_id=None):
        self._authorize(token)
        with database_connection(self.home) as db, db:
            db.execute("BEGIN")
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT result FROM editor_writing_preferences ORDER BY scope,name")]
            candidates = [json.loads(row[0]) for row in db.execute(
                "SELECT result FROM editor_writing_candidates WHERE task_id=? ORDER BY rowid", (task_id,))]
        return {"preferences": [row for row in rows if row["status"] != "deleted"],
                "candidates": [row for row in candidates if row["status"] == "candidate"]}

    def effective(self, *, token, frontier_id=None, once=None):
        self._authorize(token)
        once = {} if once is None else once
        if not isinstance(once, dict):
            raise CommandError("invalid_preference", "本次写作样式无效")
        for key, value in once.items():
            _validate(key, value)
        resolved = {key: {"id": "editor-default", "version": 1, "scope": "default", "value": values[0]}
                    for key, values in OPTIONS.items()}
        with database_connection(self.home) as db, db:
            db.execute("BEGIN")
            if frontier_id is not None:
                FrontierService.resolve_in_transaction(db, frontier_id)
            scopes = ["", frontier_id] if frontier_id else [""]
            for scope in scopes:
                rows = db.execute("SELECT name,result FROM editor_writing_preferences WHERE scope=?", (scope,))
                for key, encoded in rows:
                    row = json.loads(encoded)
                    if row["status"] == "active":
                        resolved[key] = {"id": row["id"], "version": row["version"],
                                         "scope": row["scope"], "value": row["value"],
                                         "frontier_id": row.get("frontier_id")}
        for key, value in once.items():
            resolved[key] = {"id": "editor-once", "version": 1, "scope": "once", "value": value}
        return resolved

    def change(self, command, *, token, task_id=None):
        self._authorize(token)
        operation = command.get("operation_id")
        action = command.get("action")
        scope = command.get("frontier_id")
        if (action not in ("save", "disable", "enable", "delete", "reset", "adopt", "dismiss")
                or not isinstance(operation, str) or not 1 <= len(operation) <= 128
                or (scope is not None and (not isinstance(scope, str) or not 1 <= len(scope) <= 64))):
            raise CommandError("invalid_preference", "写作偏好操作无效")
        digest = hashlib.sha256(json.dumps([task_id, command], sort_keys=True).encode()).hexdigest()
        with database_connection(self.home) as db, db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT request_digest,result FROM editor_writing_operations WHERE operation_id=?",
                               (operation,)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise CommandError("operation_conflict", "写作偏好操作标识冲突")
                return json.loads(prior[1])
            if scope is not None:
                if action == "save" or action == "adopt" or action == "enable":
                    FrontierService.resolve_in_transaction(db, scope)
                else:
                    FrontierService.read_in_transaction(db, scope)
            result = self._change(db, command, action, scope, task_id)
            db.execute("INSERT INTO editor_writing_operations VALUES(?,?,?)",
                       (operation, digest, json.dumps(result)))
            return result

    def _change(self, db, command, action, scope, task_id):
        if action == "reset":
            targets = db.execute("SELECT scope,name,result FROM editor_writing_preferences WHERE scope=?",
                                 (scope or "",)).fetchall()
            for name_scope, key, encoded in targets:
                row = json.loads(encoded)
                if row["status"] != "deleted":
                    self._persist(db, name_scope, key, {**row, "status": "deleted", "version": row["version"] + 1})
            return {"scope": "frontier" if scope else "global", "frontier_id": scope}
        candidate = None
        if action in ("adopt", "dismiss"):
            candidate_id = command.get("candidate_id")
            if not isinstance(candidate_id, str):
                raise CommandError("invalid_preference", "请选择待处理的偏好候选")
            encoded = db.execute("SELECT result FROM editor_writing_candidates WHERE candidate_id=? AND task_id=?",
                                 (candidate_id, task_id)).fetchone()
            candidate = json.loads(encoded[0]) if encoded else None
            if not candidate or candidate["status"] != "candidate":
                raise CommandError("invalid_preference", "偏好候选已不可用")
            candidate["status"] = "adopted" if action == "adopt" else "dismissed"
            db.execute("UPDATE editor_writing_candidates SET result=? WHERE candidate_id=?",
                       (json.dumps(candidate), candidate_id))
            if action == "dismiss":
                return candidate
        key = candidate["key"] if candidate else command.get("key")
        if not isinstance(key, str) or key not in OPTIONS:
            raise CommandError("invalid_preference", "请选择支持的写作样式")
        current = db.execute("SELECT result FROM editor_writing_preferences WHERE scope=? AND name=?",
                             (scope or "", key)).fetchone()
        current = json.loads(current[0]) if current else None
        exists = current is not None and current["status"] != "deleted"
        expected = command.get("expected_version")
        if type(expected) is not int or expected != (current["version"] if exists else 0):
            raise CommandError("version_conflict", "写作偏好已更新，请重新打开")
        if action in ("save", "adopt"):
            value = _validate(key, candidate["value"] if candidate else command.get("value"))
            source = (candidate["source"] if candidate else
                      {"kind": "explicit", "task_id": task_id})
            status = "active"
        elif exists:
            value, source = current["value"], current["source"]
            status = {"disable": "disabled", "enable": "active", "delete": "deleted"}[action]
        else:
            raise CommandError("not_found", "找不到写作偏好")
        result = {"id": current["id"] if current else str(uuid.uuid4()),
                  "version": (current["version"] if current else 0) + 1,
                  "scope": "frontier" if scope else "global", "frontier_id": scope,
                  "key": key, "value": value, "status": status, "source": source}
        self._persist(db, scope or "", key, result)
        return result

    @staticmethod
    def _persist(db, scope, key, result):
        db.execute("INSERT INTO editor_writing_preferences VALUES(?,?,?) "
                   "ON CONFLICT(scope,name) DO UPDATE SET result=excluded.result",
                   (scope, key, json.dumps(result)))
