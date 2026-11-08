import json
from pathlib import Path

from archive.store import Archive
from editor import model as editor_model
from horizon import model as horizon_model
from retriever import cli as retriever_cli
from writer import model as writer_model
from writer import tasks as writer_tasks


def _pipeline(kl_home, fixtures_dir, tmp_path):
    plan = horizon_model.plan(kl_home, day="2026-10-20", days=7)
    rows = retriever_cli.run(plan, "fixture", fixtures_dir, check_window=True)
    selection = editor_model.rank(kl_home, rows, day="2026-10-20", limit=4, per_checkpoint=2)
    outbox = tmp_path / "outbox"
    writer_model.write(selection, kl_home, outbox, "2026-10-20")
    task = writer_tasks.build_task(kl_home, selection, "2026-10-20")
    return task, outbox


def _push(outbox: Path) -> tuple[Path, dict]:
    path = sorted((outbox / "pushes").glob("*.json"))[0]
    return path, json.loads(path.read_text(encoding="utf-8"))


def _evidence(push: dict) -> dict:
    return {
        "paper_id": push["paper_ids"][0],
        "page": 2,
        "quote": "We show that the perturbation response is predictable.",
    }


def _proposal(push: dict, pid: str, op: str, target_type: str, target_id, **kw) -> dict:
    payload = {
        "schema_version": 2,
        "proposal_id": pid,
        "push_id": push["push_id"],
        "op": op,
        "target_type": target_type,
        "target_id": target_id,
        "diff": kw["diff"],
        "before": kw["before"],
        "after": kw["after"],
        "rationale": kw.get("rationale", "a test rationale for the proposal"),
        "confidence": kw.get("confidence", 0.6),
        "evidence": kw.get("evidence", _evidence(push)),
        "status": "draft",
    }
    return payload


def test_writer_emits_v2_proposals_that_pass_check(kl_home, fixtures_dir, tmp_path):
    task, outbox = _pipeline(kl_home, fixtures_dir, tmp_path)
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is True, result["errors"]
    _, push = _push(outbox)
    assert push["proposals"]
    for proposal in push["proposals"]:
        assert proposal["schema_version"] == 2
        assert proposal["op"] in writer_tasks.PROPOSAL_KINDS
        assert proposal["evidence"]["paper_id"] in push["paper_ids"]
        assert proposal["status"] == "draft"


def test_task_carries_history_limits_and_evidence_rules(kl_home, fixtures_dir, tmp_path):
    task, _ = _pipeline(kl_home, fixtures_dir, tmp_path)
    hmbs = next(i for i in task["items"] if i["checkpoint_id"] == "card_hmbs")
    prop = next(p for p in hmbs["context"]["propositions"] if p["id"] == "prop_hmbs_aip")
    assert prop["changelog"]
    assert task["limits"] == {"targets": 2, "per_entity": 1}
    assert task["evidence_rules"]["page"].startswith("integer")
    assert set(task["proposal_kinds"]) == set(writer_tasks.PROPOSAL_KINDS)


def test_enforces_two_entity_targets_across_cards_and_propositions(kl_home, fixtures_dir, tmp_path):
    task, outbox = _pipeline(kl_home, fixtures_dir, tmp_path)
    path, push = _push(outbox)
    hmbs_item = next(i for i in task["items"] if i["checkpoint_id"] == "card_hmbs")
    proposals = []
    for index, card_id in enumerate(["card_hmbs", "card_aip", "card_heme_biosynthesis"]):
        current = next(c for c in hmbs_item["context"]["cards"] if c["id"] == card_id)
        proposals.append(
            _proposal(
                push,
                f"p_{push['push_id']}_c{index}",
                "add_card_ontology",
                "card",
                card_id,
                diff={"op": "add", "path": "ontology", "value": f"term-{index}"},
                before=current["ontology"],
                after=current["ontology"] + [f"term-{index}"],
            )
        )
    push["proposals"] = proposals
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any("entity targets > limit 2" in e for e in result["errors"])

    for fake_id in ("prop_fake", "prop_fake2"):
        hmbs_item["context"]["propositions"].append(
            {
                "id": fake_id,
                "title": f"A fake proposition {fake_id}",
                "content": "Only used to exercise the proposition limit.",
                "related_knowledge": ["card_hmbs", "card_aip"],
                "history": [],
            }
        )
    proposals = []
    for index, prop_id in enumerate(["prop_hmbs_aip", "prop_fake", "prop_fake2"]):
        current = next(p for p in hmbs_item["context"]["propositions"] if p["id"] == prop_id)
        proposals.append(
            _proposal(
                push,
                f"p_{push['push_id']}_p{index}",
                "update_proposition_title",
                "proposition",
                prop_id,
                diff={"op": "replace", "path": "title", "value": f"new title {index}"},
                before=current["title"],
                after=f"new title {index}",
            )
        )
    push["proposals"] = proposals
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any("entity targets > limit 2" in e for e in result["errors"])


def test_evidence_must_come_from_the_pushed_paper(kl_home, fixtures_dir, tmp_path):
    task, outbox = _pipeline(kl_home, fixtures_dir, tmp_path)
    path, push = _push(outbox)
    proposal = next(
        p for p in push["proposals"] if p["op"] == "add_proposition_evidence"
    )
    proposal["diff"]["value"]["papers"][0]["paper_id"] = "made_up_paper"
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any("not the pushed paper" in e for e in result["errors"])


def test_create_proposition_needs_two_known_cards(kl_home, fixtures_dir, tmp_path):
    task, outbox = _pipeline(kl_home, fixtures_dir, tmp_path)
    path, push = _push(outbox)
    push["proposals"] = [
        _proposal(
            push,
            f"p_{push['push_id']}_01",
            "create_proposition",
            "none",
            None,
            diff={
                "op": "add",
                "path": "proposition",
                "value": {
                    "title": "A new cross-paper claim",
                    "content": "A sufficiently long claim body for the validator.",
                    "related_knowledge": ["card_hmbs"],
                },
            },
            before=None,
            after={"title": "A new cross-paper claim"},
        )
    ]
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any(">=2 related_knowledge" in e for e in result["errors"])


def test_stale_before_is_rejected(kl_home, fixtures_dir, tmp_path):
    task, outbox = _pipeline(kl_home, fixtures_dir, tmp_path)
    path, push = _push(outbox)
    push["proposals"] = [
        _proposal(
            push,
            f"p_{push['push_id']}_01",
            "update_card_annotation",
            "card",
            "card_hmbs",
            diff={"op": "replace", "path": "annotation", "value": "a new annotation that is long enough"},
            before="stale annotation",
            after="a new annotation that is long enough",
        )
    ]
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any("before does not match card annotation" in e for e in result["errors"])


def test_archive_applies_every_v3_op_and_is_idempotent(kl_home):
    store = Archive(kl_home)
    store.init()
    card = store.get_card("card_hmbs")
    prop = store.get_prop("prop_hmbs_aip")
    push_id = "push_contract_01"
    quote = "HMBS loss impairs heme biosynthesis in hepatocytes"
    evidence = {"paper_id": "p1", "page": 3, "quote": quote}

    def ev(ev_id: str, paper_id: str = "p1") -> dict:
        return {
            "id": ev_id,
            "stance": "supports",
            "conclusion": "HMBS 缺失会降低血红素合成。",
            "papers": [{"paper_id": paper_id, "page": 3, "quote": quote}],
        }

    proposals = [
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_01",
            "add_card_ontology",
            "card",
            "card_hmbs",
            diff={"op": "add", "path": "ontology", "value": "PBGD enzyme"},
            before=card["ontology"],
            after=card["ontology"] + ["PBGD enzyme"],
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_02",
            "update_card_annotation",
            "card",
            "card_hmbs",
            diff={
                "op": "replace",
                "path": "annotation",
                "value": card["annotation"] + "\n新增注释。",
            },
            before=card["annotation"],
            after=card["annotation"] + "\n新增注释。",
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_03",
            "create_card",
            "none",
            None,
            diff={
                "op": "add",
                "path": "card",
                "value": {
                    "id": "card_heme_trafficking",
                    "label": "heme trafficking",
                    "ontology": ["heme trafficking"],
                    "annotation": "血红素在细胞内的转运与分配。",
                },
            },
            before=None,
            after={"label": "heme trafficking"},
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_04",
            "create_proposition",
            "none",
            None,
            diff={
                "op": "add",
                "path": "proposition",
                "value": {
                    "id": "prop_hmbs_heme_trafficking",
                    "title": "HMBS 影响血红素转运",
                    "review": "HMBS 功能缺失会改变血红素在细胞内的转运与分配。",
                    "related_knowledge": ["card_hmbs", "card_heme_biosynthesis"],
                    "evidence": [ev("ev_hmbs_heme_trafficking_01", "p1"), ev("ev_hmbs_heme_trafficking_02", "p2")],
                },
            },
            before=None,
            after={"title": "HMBS 影响血红素转运"},
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_05",
            "update_proposition_title",
            "proposition",
            prop["id"],
            diff={"op": "replace", "path": "title", "value": "HMBS 缺失导致 AIP（修订）"},
            before=prop["title"],
            after="HMBS 缺失导致 AIP（修订）",
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_06",
            "update_proposition_review",
            "proposition",
            prop["id"],
            diff={
                "op": "replace",
                "path": "review",
                "value": "修订后的综述：HMBS 功能缺失导致 AIP，现有证据以病例与酶学测定为主。",
            },
            before=prop["review"],
            after="修订后的综述：HMBS 功能缺失导致 AIP，现有证据以病例与酶学测定为主。",
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_07",
            "add_proposition_evidence",
            "proposition",
            prop["id"],
            diff={"op": "add", "path": "evidence", "value": ev("ev_hmbs_aip_01")},
            before=len(prop["evidence"]),
            after=len(prop["evidence"]) + 1,
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_08",
            "merge_proposition_evidence",
            "proposition",
            prop["id"],
            diff={
                "op": "merge",
                "path": "evidence",
                "value": {
                    "target_evidence_id": "ev_hmbs_aip_01",
                    "papers": [
                        {"paper_id": "p3", "page": 2, "quote": quote}
                    ],
                },
            },
            before={"conclusion": "HMBS 缺失会降低血红素合成。", "papers": ["p1"]},
            after={"conclusion": "HMBS 缺失会降低血红素合成。", "papers": ["p1", "p3"]},
            evidence=evidence,
        ),
        _proposal(
            {"push_id": push_id, "paper_ids": ["p1"]},
            "p_contract_09",
            "update_proposition_related_knowledge",
            "proposition",
            prop["id"],
            diff={
                "op": "replace",
                "path": "related_knowledge",
                "value": ["card_hmbs", "card_heme_biosynthesis"],
            },
            before=prop["related_knowledge"],
            after=["card_hmbs", "card_heme_biosynthesis"],
            evidence=evidence,
        ),
    ]
    store.add_push({"push_id": push_id, "paper_ids": ["p1"], "proposals": proposals})
    for proposal in proposals:
        store.apply_proposal(push_id, proposal["proposal_id"])

    assert "PBGD enzyme" in store.get_card("card_hmbs")["ontology"]
    assert store.get_card("card_hmbs")["annotation"].endswith("新增注释。")
    assert store.get_card("card_heme_trafficking")["label"] == "heme trafficking"

    updated = store.get_prop("prop_hmbs_aip")
    assert updated["title"].endswith("（修订）")
    assert updated["review"].startswith("修订后的综述")
    assert updated["related_knowledge"] == ["card_hmbs", "card_heme_biosynthesis"]
    assert len(updated["evidence"]) == 1
    assert [p["paper_id"] for p in updated["evidence"][0]["papers"]] == ["p1", "p3"]
    assert updated["evidence_updates_since_review"] == 2  # review reset, then add + merge
    assert updated["changelog"][-1]["action"] == "related_knowledge_changed"

    created = store.get_prop("prop_hmbs_heme_trafficking")
    assert len(created["evidence"]) == 2

    again = store.apply_proposal(push_id, "p_contract_07")
    assert again["deduped"] is True
    assert len(store.get_prop("prop_hmbs_aip")["evidence"]) == 1


def test_mixed_card_and_proposition_targets_share_one_pool(
    kl_home, fixtures_dir, tmp_path
):
    task, outbox = _pipeline(kl_home, fixtures_dir, tmp_path)
    path, push = _push(outbox)
    item = next(i for i in task["items"] if i["checkpoint_id"] == "card_hmbs")
    card = next(c for c in item["context"]["cards"] if c["id"] == "card_hmbs")
    prop = next(
        p for p in item["context"]["propositions"] if p["id"] == "prop_hmbs_aip"
    )
    push["proposals"] = [
        _proposal(
            push,
            f"p_{push['push_id']}_01",
            "add_card_ontology",
            "card",
            "card_hmbs",
            diff={"op": "add", "path": "ontology", "value": "PBG deaminase"},
            before=card["ontology"],
            after=card["ontology"] + ["PBG deaminase"],
        ),
        _proposal(
            push,
            f"p_{push['push_id']}_02",
            "update_proposition_title",
            "proposition",
            "prop_hmbs_aip",
            diff={"op": "replace", "path": "title", "value": "修订后的标题"},
            before=prop["title"],
            after="修订后的标题",
        ),
        _proposal(
            push,
            f"p_{push['push_id']}_03",
            "update_proposition_title",
            "proposition",
            "prop_hmbs_aip",
            diff={"op": "replace", "path": "title", "value": "同一目标的另一条操作"},
            before=prop["title"],
            after="同一目标的另一条操作",
        ),
    ]
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    # Two proposals targeting the same proposition still count as one target.
    assert result["ok"] is True, result["errors"]

    push["proposals"].append(
        _proposal(
            push,
            f"p_{push['push_id']}_04",
            "update_card_annotation",
            "card",
            "card_aip",
            diff={
                "op": "replace",
                "path": "annotation",
                "value": "A sufficiently long replacement annotation for the validator.",
            },
            before=next(
                c for c in item["context"]["cards"] if c["id"] == "card_aip"
            )["annotation"],
            after="A sufficiently long replacement annotation for the validator.",
        )
    )
    path.write_text(json.dumps(push, ensure_ascii=False), encoding="utf-8")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any("entity targets > limit 2" in e for e in result["errors"])
