"""Cross-owner knowledge history, undo preview and non-covering undo.

The three knowledge owners already commit every mutation together with an
immutable operation receipt that stores the full snapshot it produced and the
request that produced it. History is therefore derived by joining that version
chain by operation id; there is no backfill, shadow store or copied history
table. Undo commits a new owner version from trusted historical editable fields
and leaves every earlier snapshot, receipt and event untouched.

This module is the shared helper the owners expose through their ``HISTORY``
table metadata and callbacks. It never imports an owner, so owners can import
``validate_reason`` and ``set_path`` without a cycle. The desktop bridge is the
only public caller.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager

from hostrun.agent_service import CommandError
from hostrun.managed_database import database_connection

KINDS = ("card", "theme", "frontier")
_UNDOABLE_ACTIONS = ("update", "undo")
REASON_BYTES = 500
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 20
_MAX_ID_BYTES = 256
_PREVIEW_NAMESPACE = "horizon.knowledge.undo.v1"
_PREVIEW_KEY = secrets.token_bytes(32)


def validate_reason(value, *, code):
    """Validate the optional bounded reason stored in a mutation receipt."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() \
            or len(value.encode("utf-8")) > REASON_BYTES:
        raise CommandError(code, "reason must be a bounded non-empty string")
    return value


def get_path(record, path):
    node = record
    for key in path:
        node = node[key]
    return node


def set_path(record, path, value):
    node = record
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = copy.deepcopy(value)


class KnowledgeHistoryService:
    """Authenticated history, preview and undo over the three owners."""

    def __init__(self, services):
        self.services = dict(services)

    @contextmanager
    def _connect(self, service):
        with database_connection(service.home) as db:
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _authorize(self, service, token):
        # The owner holds the trusted launch token; the helper never compares a
        # caller-supplied value with itself.
        service._authorize(token)

    def _owner(self, kind):
        if not isinstance(kind, str) or kind not in KINDS:
            raise CommandError("invalid_command",
                               "kind must be card, theme or frontier")
        service = self.services.get(kind)
        if service is None:
            raise CommandError("invalid_command", f"{kind} owner is unavailable")
        return service, service.HISTORY

    @staticmethod
    def _identity(command):
        if not isinstance(command, dict) \
                or type(command.get("schema_version")) is not int \
                or command["schema_version"] != 1 \
                or not isinstance(command.get("operation_id"), str) \
                or not command["operation_id"].strip():
            raise CommandError("invalid_command",
                               "schema_version and operation_id required")
        identifier = command.get("id")
        if not isinstance(identifier, str) or not identifier.strip() \
                or len(identifier.encode("utf-8")) > _MAX_ID_BYTES:
            raise CommandError("invalid_command", "id must be a bounded identifier")
        return command.get("kind"), identifier, command["operation_id"]

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= _MAX_LIMIT:
            raise CommandError("invalid_command",
                               f"limit must be an integer from 1 to {_MAX_LIMIT}")
        if type(offset) is not int or offset < 0:
            raise CommandError("invalid_command",
                               "offset must be a nonnegative integer")

    @staticmethod
    def _target_version(value):
        if type(value) is not int or value < 1:
            raise CommandError("invalid_command",
                               "target_version must be a positive integer")
        return value

    @staticmethod
    def _operation_items(db, meta, identifier):
        """Join the immutable receipts into a version chain for one record."""
        rows = db.execute(
            f"SELECT request, result FROM {meta['operations']} "
            "WHERE json_extract(result, ?) = ? ORDER BY rowid",
            (f"$.{meta['id_field']}", identifier),
        ).fetchall()
        by_version = {}
        items = []
        for request, result in rows:
            try:
                snapshot = json.loads(result)
            except (TypeError, ValueError):
                continue
            if not isinstance(snapshot, dict) \
                    or snapshot.get(meta["id_field"]) != identifier:
                continue
            version = snapshot.get("version")
            if type(version) is not int or version in by_version:
                continue
            by_version[version] = snapshot
            try:
                data = json.loads(request) if isinstance(request, str) else {}
            except (TypeError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            action = data.get("action")
            command = data.get("command")
            if not isinstance(command, dict):
                command = {}
            reason = command.get("reason")
            target_version = command.get("target_version")
            items.append({
                "action": action if isinstance(action, str) else "update",
                "reason": reason if isinstance(reason, str) else None,
                "target_version": target_version
                if type(target_version) is int else None,
                "after": snapshot,
            })
        for item in items:
            item["before"] = by_version.get(item["after"]["version"] - 1)
        items.sort(key=lambda item: item["after"]["version"], reverse=True)
        return items

    @staticmethod
    def _entry(service, item, current, removed):
        project = service.history_project
        before = item["before"]
        return {
            "version": item["after"]["version"],
            "action": item["action"],
            "actor": "explorer",
            "time": item["after"].get("updated_at"),
            "reason": item["reason"],
            "target_version": item["target_version"],
            "before": project(before, removed) if before is not None else None,
            "after": project(item["after"], removed),
            "can_preview": item["action"] in _UNDOABLE_ACTIONS
            and item["before"] is not None
            and service.history_undoable(current),
        }

    def history(self, command, *, token):
        """Read the committed version chain, newest first, for one record."""
        kind, identifier, operation_id = self._identity(command)
        service, meta = self._owner(kind)
        self._authorize(service, token)
        limit = command.get("limit", _DEFAULT_LIMIT)
        offset = command.get("offset", 0)
        self._page(limit, offset)
        with self._connect(service) as db:
            db.execute("BEGIN")
            current = service._read(db, identifier)
            removed = service.history_removed(db)
            items = self._operation_items(db, meta, identifier)
            entries = [self._entry(service, item, current, removed)
                       for item in items[offset:offset + limit]]
        return {
            "operation_id": operation_id,
            "kind": kind,
            "id": identifier,
            "current_version": current["version"],
            "total": len(items),
            "offset": offset,
            "limit": limit,
            "entries": entries,
        }

    @staticmethod
    def _inverse_changes(meta, before, after, current, later):
        """Inverse patch per owner-defined field, preserving unrelated edits."""
        changes = []
        conflict = False
        for path in meta["editable"]:
            old = get_path(before, path)
            new = get_path(after, path)
            now = get_path(current, path)
            if old == new:
                continue  # The selected edit did not touch this field.
            touched_later = any(item["before"] is None or
                                get_path(item["before"], path) != get_path(item["after"], path)
                                for item in later)
            if now == old:
                proposed, reverted, flagged = now, True, False
            elif now == new and not touched_later:
                proposed, reverted, flagged = old, False, False
            else:
                proposed, reverted, flagged = old, False, True
                conflict = True
            changes.append({
                "path": path, "before": old, "after": new, "current": now,
                "proposed": proposed, "reverted": reverted, "conflict": flagged,
            })
        return changes, conflict

    @staticmethod
    def _display_changes(service, changes, before, after, current, removed):
        project = service.history_project
        projected_before = project(before, removed)
        projected_after = project(after, removed)
        projected_current = project(current, removed)
        displayed = []
        for change in changes:
            path = change["path"]
            displayed.append({
                "field": ".".join(path),
                "before": get_path(projected_before, path),
                "after": get_path(projected_after, path),
                "current": get_path(projected_current, path),
                "proposed": get_path(projected_current, path)
                if change["reverted"] else get_path(projected_before, path),
                "conflict": change["conflict"],
            })
        return displayed

    @staticmethod
    def _preview_token(kind, identifier, target_version, current_version,
                       changes):
        payload = {
            "namespace": _PREVIEW_NAMESPACE,
            "kind": kind,
            "id": identifier,
            "target_version": target_version,
            "current_version": current_version,
            "changes": changes,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        return hmac.new(_PREVIEW_KEY, encoded, hashlib.sha256).hexdigest()

    def preview(self, command, *, token):
        """Review the inverse patch and a token bound to that reviewed plan."""
        kind, identifier, operation_id = self._identity(command)
        service, meta = self._owner(kind)
        self._authorize(service, token)
        target_version = self._target_version(command.get("target_version"))
        with self._connect(service) as db:
            db.execute("BEGIN")
            current = service._read(db, identifier)
            plan, _ = self._plan(db, service, meta, kind, identifier,
                                 target_version, current)
        return {"operation_id": operation_id, **plan}

    def _plan(self, db, service, meta, kind, identifier, target_version, current):
        items = self._operation_items(db, meta, identifier)
        target = next((item for item in items
                       if item["after"]["version"] == target_version), None)
        result = {
            "kind": kind,
            "id": identifier,
            "target_version": target_version,
            "current_version": current["version"],
            "preview_token": None,
            "can_undo": False,
            "code": None,
            "changes": [],
        }
        if target is None:
            result["code"] = "not_found"
            return result, None
        if target["action"] not in _UNDOABLE_ACTIONS or target["before"] is None:
            result["code"] = "undo_not_supported"
            return result, None
        later = [item for item in items if target_version < item["after"]["version"] <= current["version"]]
        changes, conflict = self._inverse_changes(
            meta, target["before"], target["after"], current, later)
        removed = service.history_removed(db)
        result["changes"] = self._display_changes(
            service, changes, target["before"], target["after"], current,
            removed)
        if not service.history_undoable(current):
            result["code"] = "invalid_transition"
            return result, None
        elif conflict:
            result["code"] = "undo_conflict"
            return result, None

        restored = {change["path"]: change["before"]
                    for change in changes if not change["reverted"]}
        if not restored:
            result["code"] = "nothing_to_undo"
            return result, None
        try:
            updated = service.apply_history_undo(db, current, restored)
        except CommandError as exc:
            result["code"] = exc.code
            result["message"] = str(exc)
            return result, None

        result["can_undo"] = True
        result["preview_token"] = self._preview_token(
            kind, identifier, target_version, current["version"], result["changes"])
        return result, updated

    @staticmethod
    def _encoded_receipt(command):
        stored = {key: value for key, value in command.items()
                  if key != "action"}
        return json.dumps({"action": "undo", "command": stored}, sort_keys=True)

    @staticmethod
    def _replay(db, meta, operation_id, encoded):
        row = db.execute(
            f"SELECT request, result FROM {meta['operations']}"
            " WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None:
            return None
        if row[0] != encoded:
            raise CommandError("operation_conflict", "operation_id conflicts")
        return json.loads(row[1])

    def undo(self, command, *, token):
        """Commit a new owner version from trusted historical editable fields."""
        kind, identifier, operation_id = self._identity(command)
        service, meta = self._owner(kind)
        self._authorize(service, token)
        encoded = self._encoded_receipt(command)

        with self._connect(service) as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._replay(db, meta, operation_id, encoded)
            if previous is not None:
                return previous

            target_version = self._target_version(command.get("target_version"))
            expected_version = command.get("expected_version")
            preview_token = command.get("preview_token")
            if type(expected_version) is not int:
                raise CommandError("invalid_command",
                                   "expected_version must be an integer")
            if not isinstance(preview_token, str) or not preview_token or not preview_token.isascii():
                raise CommandError("invalid_command", "preview_token required")
            validate_reason(command.get("reason"), code="invalid_command")

            current = service._read(db, identifier)
            if expected_version != current["version"]:
                error = CommandError("version_conflict",
                                     "知识记录已被修改，请刷新后重试")
                error.details = {
                    "expected_version": expected_version,
                    "current_version": current["version"],
                    "current": current,
                }
                raise error

            plan, updated = self._plan(db, service, meta, kind, identifier,
                                       target_version, current)
            if not plan["can_undo"]:
                message = {
                    "undo_conflict": "后续编辑与此前字段重叠，请先核对差异",
                    "nothing_to_undo": "当前字段已为原值",
                }.get(plan["code"], "该版本无法撤销，请重新核对历史")
                error = CommandError(plan["code"], plan.get("message", message))
                error.details = plan
                raise error
            if not hmac.compare_digest(preview_token, plan["preview_token"]):
                raise CommandError("preview_conflict",
                                   "撤销预览已变化，请重新查看差异")

            updated["operation_id"] = operation_id
            updated["version"] = current["version"] + 1
            updated["updated_at"] = int(service.clock())

            db.execute(
                f"UPDATE {meta['table']} SET result=? WHERE {meta['id_field']}=?",
                (json.dumps(updated), identifier))
            db.execute(
                f"INSERT INTO {meta['operations']} VALUES(?,?,?)",
                (operation_id, encoded, json.dumps(updated)))
            event = {
                "schema_version": 1,
                "type": meta["event_type"],
                meta["id_field"]: identifier,
                "operation_id": operation_id,
                "version": updated["version"],
                "target_version": target_version,
            }
            db.execute(
                f"INSERT INTO {meta['events']}(operation_id,event) VALUES(?,?)",
                (operation_id, json.dumps(event)))
        service._notify(updated)
        return updated
