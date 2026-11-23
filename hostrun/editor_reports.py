"""Read-only source evidence for Editor report generation."""

import hashlib
import hmac
import json
import re
import uuid

from hostrun.agent_service import CommandError
from hostrun.voyager_materials import MaterialsService, citation_anchors
from hostrun.managed_database import database_connection


DEFAULT_PREFERENCE_VERSION = {"id": "editor-default", "version": 1}


def report_evidence(materials, material_id, task_id, *, token):
    package = materials.get_package(material_id, task_id=task_id, token=token)
    academic = package.get("academic")
    text = package["original_text"]
    limitation = None
    authorization = None
    if academic and academic["status"] != "available":
        authorizations = academic["abstract_authorizations"]
        if academic["status"] != "pending_fulltext" or not authorizations:
            raise CommandError("report_source_unavailable", "资料没有可读取的全文或本任务的摘要授权")
        authorization = authorizations[-1]
        text = authorization["abstract"]
        limitation = authorization["limitation"]
    if package.get("archived") or not text.strip():
        raise CommandError("report_source_unavailable", "资料没有可读取的正文")
    if authorization is None:
        locators = [{"locator": f"page {anchor['page']}" if 'page' in anchor else anchor['id'],
                     "text": text[anchor['start']:anchor['end']]}
                    for anchor in citation_anchors(package, text)]
    else:
        locators = [{"locator": "abstract", "text": text}]
    return {
        "locators": locators,
        "material_id": material_id,
        "material_version": package["version"],
        "title": package["title"],
        "source": package["source"],
        "snapshot": package["snapshot"] if authorization is None else None,
        "abstract_authorization": authorization,
        "text": text,
        "limitation": limitation,
    }



def build_editor_context(evidence, known):
    """Project an explicitly limited prefix of a large captured source."""
    source = {key: evidence[key] for key in ("title", "snapshot", "limitation")}
    locators = []
    budget = 42_000
    for segment in evidence["locators"]:
        encoded = json.dumps(segment, ensure_ascii=False).encode("utf8")
        if len(encoded) <= budget:
            locators.append(segment)
            budget -= len(encoded) + 2
            continue
        if budget > 250:
            limit = min(budget - 130, len(segment["text"]))
            while limit and len(json.dumps({**segment, "text": segment["text"][:limit]},
                                           ensure_ascii=False).encode("utf8")) > budget:
                limit //= 2
            if limit:
                locators.append({**segment, "text": segment["text"][:limit]})
        notice = "资料超出本次模型可读范围。报道仅覆盖提供的前部快照片段。"
        source["limitation"] = " ".join(filter(None, (source["limitation"], notice)))
        break
    source["locators"] = locators
    return {"source": source, "knowledge": known, "preferences": [DEFAULT_PREFERENCE_VERSION]}


def read_report_response(response):
    """Parse one JSON object, including provider responses wrapped in a JSON fence."""
    response = response.strip()
    fence = re.fullmatch(r"```(?:json)?[^\S\n]*\n(.*?)\n```", response, re.DOTALL | re.IGNORECASE)
    if fence:
        response = fence[1]
    return json.loads(response)["claims"]


class ReportService:
    """Persist immutable Editor reports and their source references together."""

    def __init__(self, home, *, token):
        self.home = home
        self._token = token
        self.materials = MaterialsService(home, token=token)
        with database_connection(self.home) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS editor_reports(
                report_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
                task_id TEXT NOT NULL, request_digest TEXT NOT NULL, result TEXT NOT NULL)""")

    def _authorize(self, token):
        if not isinstance(token, str) or not hmac.compare_digest(token, self._token):
            raise PermissionError("Unauthorized Editor command")

    def save(self, operation_id, task_id, evidence, context_versions, claims, *, token):
        self._authorize(token)
        if not isinstance(claims, list) or not 1 <= len(claims) <= 40:
            raise CommandError("invalid_report", "报道需要至少一条有效判断")
        request = [task_id, evidence, context_versions, claims]
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        with database_connection(self.home) as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT request_digest, result FROM editor_reports WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if previous:
                if previous[0] != digest:
                    raise CommandError("operation_conflict", "操作标识与已保存报道冲突")
                return json.loads(previous[1])

            current = report_evidence(self.materials, evidence["material_id"], task_id, token=token)
            if current != evidence:
                raise CommandError("version_conflict", "资料或摘要授权已更新，请重新生成报道")

            report_id = str(uuid.uuid4())
            projection = build_editor_context(evidence, [])["source"]
            result = {
                "report_id": report_id, "version": 1, "task_id": task_id,
                "title": evidence["title"], "context_versions": context_versions,
                "limitation": projection["limitation"],
                "source": {key: evidence[key] for key in
                           ("material_id", "material_version", "snapshot", "source")},
                "claims": [self._claim(db, claim, evidence, projection, report_id, task_id, token) for claim in claims],
            }
            db.execute("INSERT INTO editor_reports VALUES(?,?,?,?,?)",
                       (report_id, operation_id, task_id, digest, json.dumps(result, ensure_ascii=False)))
            return result

    def _claim(self, db, claim, evidence, projection, report_id, task_id, token):
        if not isinstance(claim, dict):
            raise CommandError("invalid_report", "报道判断必须为结构化对象")
        kind = claim.get("kind")
        citations = claim.get("citations", [] if kind == "unknown" else None)
        if (kind not in ("source", "inference", "unknown")
                or not isinstance(claim.get("text"), str) or not claim["text"].strip()
                or len(claim["text"]) > 4000 or not isinstance(citations, list)
                or len(citations) > 10 or (kind != "unknown" and not citations)):
            raise CommandError("invalid_report", "报道判断缺少类型、正文或必要依据")
        references = []
        for item in citations:
            if (not isinstance(item, dict) or not isinstance(item.get("excerpt"), str)
                    or not item["excerpt"].strip() or len(item["excerpt"]) > 2000
                    or not isinstance(item.get("locator"), str) or len(item["locator"]) > 50):
                raise CommandError("invalid_report", "引用需要可核对的摘录与定位")
            excerpt, locator = item["excerpt"], item["locator"]
            if projection["limitation"] != evidence["limitation"] and not any(
                    ref["locator"] == locator and excerpt in ref["text"] for ref in projection["locators"]):
                raise CommandError("invalid_source", "引用不在本次提供的快照片段中")
            authorization = evidence["abstract_authorization"]
            if authorization is None and not re.fullmatch(r"p[1-9][0-9]*|page [1-9][0-9]*", locator):
                raise CommandError("invalid_source", "引用需要真实快照页码或段落编号")
            citation = {"material_id": evidence["material_id"],
                        "material_version": evidence["material_version"],
                        "excerpt": excerpt, "locator": locator}
            references.append(self.materials.report_reference(
                db, citation, report_id=report_id, task_id=task_id,
                abstract_operation=authorization["operation_id"] if authorization else None,
                token=token))
        return {"kind": claim["kind"], "text": claim["text"], "citations": references}

    def get_by_operation(self, operation_id, task_id, material_id, *, token):
        self._authorize(token)
        with database_connection(self.home) as db, db:
            row = db.execute("SELECT result FROM editor_reports WHERE operation_id=? AND task_id=?",
                             (operation_id, task_id)).fetchone()
        if row is None:
            return None
        result = json.loads(row[0])
        if result["source"]["material_id"] != material_id:
            raise CommandError("operation_conflict", "操作标识与已保存资料冲突")
        return result

    def get(self, report_id, task_id, *, token):
        report = self._read(report_id, task_id, token=token)
        for claim in report["claims"]:
            for citation in claim["citations"]:
                if citation.get("status") == "abstract":
                    view = self._open_abstract(report, citation, token=token)
                    if view["status"] != "abstract":
                        citation["excerpt"] = "原摘要不可用"
        return report

    def _read(self, report_id, task_id, *, token):
        self._authorize(token)
        with database_connection(self.home) as db, db:
            row = db.execute("SELECT result FROM editor_reports WHERE report_id=? AND task_id=?", (report_id, task_id)).fetchone()
        if not row:
            raise CommandError("not_found", "找不到报道")
        return json.loads(row[0])

    def _open_abstract(self, report, citation, *, token):
        owner = f"report:{report['report_id']}:{report['version']}:{citation['content_hash']}"
        return self.materials.report_abstract_view(citation, owner, report["task_id"], token=token)

    def citation(self, report_id, task_id, claim_index, citation_index, *, token, snapshot_offset=None):
        report = self._read(report_id, task_id, token=token)
        try:
            if claim_index < 0 or citation_index < 0:
                raise IndexError
            citation = report["claims"][claim_index]["citations"][citation_index]
        except (IndexError, TypeError):
            raise CommandError("not_found", "找不到报道引用") from None
        owner = f"report:{report_id}:{report['version']}:{citation['content_hash']}"
        if citation.get("status") == "abstract":
            return self._open_abstract(report, citation, token=token)
        if snapshot_offset is not None:
            if type(snapshot_offset) is not int or snapshot_offset < 0:
                raise CommandError("invalid_offset", "快照位置无效")
            reference = self.materials.resolve_reference(citation["material_id"], owner, token=token)
            text = reference["original_text"]
            end = snapshot_offset + 12_000
            return {"status": reference["status"], "segment": text[snapshot_offset:end] if text else None,
                    "snapshot_offset": snapshot_offset, "next_offset": end if text and end < len(text) else None}
        return self.materials.citation_view(citation, owner, token=token)
