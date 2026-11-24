"""Editor context for a bounded, question-led synthesis of selected material."""

import json

from hostrun.agent_service import CommandError
from hostrun.editor_reports import (DEFAULT_PREFERENCE_VERSION, SYNTHESIS_SOURCE_BUDGET,
                                    build_editor_context, evidence_provenance, report_evidence)
from hostrun.knowledge_cards import KnowledgeCardService
from hostrun.voyager_service import VoyagerService


def synthesis_input(value):
    if not isinstance(value, str) or not value.lstrip().startswith("{"):
        return None
    try:
        command = json.loads(value)
    except ValueError:
        return None
    if not isinstance(command, dict) or "material_ids" not in command:
        return None
    ids, question = command["material_ids"], command.get("question")
    if (not isinstance(ids, list) or not 2 <= len(ids) <= 4
            or any(not isinstance(mid, str) or not 1 <= len(mid) <= 64 for mid in ids)
            or len(set(ids)) != len(ids) or not isinstance(question, str)
            or not 1 <= len(question.strip()) <= 500
            or type(command.get("expand_known", False)) is not bool
            or (command.get("retrieval_id") is not None and
                (not isinstance(command["retrieval_id"], str) or len(command["retrieval_id"]) > 64))):
        raise CommandError("invalid_report", "综合报道需要一个问题和两到四份不同资料")
    return command


def confirmed_knowledge(home, *, token):
    cards = KnowledgeCardService(home, token=token).query(token=token)
    return [{"kind": "card", "id": item["card_id"], "version": item["version"],
              "title": item["title"][:120], "summary": item["content"][:500]}
             for item in cards if item["known"] and not item["source_pending"] and not item["questionable"]
             and item["sources"] and all(ref.get("status") != "removed" for ref in item["sources"])][:8]


def prepare_synthesis(reports, home, task_id, command, *, token):
    evidence, gaps, unavailable_origins = [], [], []
    for mid in command["material_ids"]:
        try:
            evidence.append(report_evidence(reports.materials, mid, task_id, token=token))
        except CommandError as error:
            if error.code not in ("report_source_unavailable", "not_found"):
                raise
            gaps.append({"material_id": mid, "reason": "unavailable"})
            if error.code == "report_source_unavailable":
                try:
                    package = reports.materials.get_package(mid, task_id=task_id, token=token)
                except CommandError as missing:
                    if missing.code != "not_found":
                        raise
                else:
                    unavailable_origins.extend(evidence_provenance(package))
    if not evidence:
        raise CommandError("report_source_unavailable", "没有可用于综合的资料")
    reposted = set()
    retrieval_id = command.get("retrieval_id")
    retrievals = {row["request_id"] for item in evidence for row in item["provenance"]}
    retrievals.update(row["request_id"] for row in unavailable_origins)
    coverage = []
    if retrieval_id:
        retrievals.add(retrieval_id)
    if retrievals:
        voyager = VoyagerService(home, token=token)
    for identity in sorted(retrievals):
        try:
            request = voyager.get(identity, token=token)
        except CommandError as error:
            if identity == retrieval_id or error.code != "not_found":
                raise
            continue
        if request.get("task_id") != task_id:
            if identity == retrieval_id:
                raise CommandError("invalid_task", "本会话没有该检索的使用权限")
            continue
        reposted.update((row["source"], row["source_id"]) for row in request["candidates"]
                        if (row.get("selection_reason") or {}).get("reposted"))
        if request["status"] in ("planned", "running", "cancelled"):
            coverage.append({"reason": "retrieval_incomplete", "retrieval_id": identity,
                             "detail": request["status"]})
        else:
            coverage.extend({"reason": "retrieval_gap", "retrieval_id": identity,
                             "source": gap.get("source"), "detail": gap.get("reason")}
                            for gap in request["gaps"])
            if request["status"] != "complete" and not request["gaps"]:
                coverage.append({"reason": "retrieval_incomplete", "retrieval_id": identity,
                                 "detail": request.get("stop_reason") or request["status"]})
    gaps.extend(coverage[:12])
    if len(coverage) > 12:
        gaps.append({"reason": "more_gaps", "count": len(coverage) - 12})
    reposts = [item["material_id"] for item in evidence
               if any((row["source"], row["source_id"]) in reposted for row in item["provenance"])]
    known = confirmed_knowledge(home, token=token)
    versions = {"knowledge": [{key: item[key] for key in ("kind", "id", "version")} for item in known],
                "preferences": [DEFAULT_PREFERENCE_VERSION]}
    sources = [{**build_editor_context(item, [], budget=SYNTHESIS_SOURCE_BUDGET // len(evidence))["source"],
                "material_id": item["material_id"], "repost": item["material_id"] in reposts}
               for item in evidence]
    context = {"mode": "synthesis", "question": command["question"], "sources": sources,
               "gaps": gaps, "confirmed_knowledge": known,
               "expand_known": command.get("expand_known", False), "preferences": [DEFAULT_PREFERENCE_VERSION]}
    return context, evidence, versions, reposts


def commit_synthesis(reports, operation, task_id, command, context, evidence, versions, reposts, result, *, token, task_version=None):
    if (result.get("outcome") == "no_new" and not command.get("report_id")
            and not command.get("expand_known", False)):
        if result.get("claims"):
            raise CommandError("invalid_report", "无新增结果不能包含报道正文")
        return {"outcome": "no_new", "gaps": context["gaps"]}
    options = {key:command[key] for key in ("expand_known", "retrieval_id") if command.get(key)}
    report = reports.save_synthesis(operation, task_id, command["question"], evidence, versions,
        result.get("claims"), gaps=context["gaps"], reposts=reposts,
        selected_material_ids=command["material_ids"], token=token,
        report_id=command.get("report_id"), base_version=command.get("base_version"),
        synthesis_options=options, keeper_proposals=result.get("keeper_proposals"), task_version=task_version)
    return {"report_id": report["report_id"], "version": report["version"]}
