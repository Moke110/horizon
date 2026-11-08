import json
from pathlib import Path

import pytest

from archive import migrate
from archive.store import Archive, ArchiveError
from conftest import cli_locations


def test_add_paper_dedupes_by_source_id(kl_home):
    store = Archive(kl_home)
    store.init()
    rec = {
        "source": "europepmc",
        "source_id": "42900001",
        "title": "HMBS loss",
        "abstract": "abstract",
        "checkpoint_id": "card_hmbs",
    }
    first = store.add_paper(dict(rec))
    second = store.add_paper(dict(rec))
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert second["paper_id"] == first["paper_id"]
    assert store.get_paper(first["paper_id"])["title"] == "HMBS loss"
    assert (kl_home / "archive" / "raw" / "text" / f"{first['paper_id']}.txt").exists()


def test_context_aggregates_card_props_and_history(kl_home):
    store = Archive(kl_home)
    store.init()
    store.add_paper(
        {
            "source": "europepmc",
            "source_id": "1",
            "title": "t",
            "abstract": "a",
            "checkpoint_id": "card_hmbs",
        }
    )
    ctx = store.context("card_hmbs")
    assert ctx["card"]["id"] == "card_hmbs"
    assert ctx["archived_count"] == 1
    assert {p["id"] for p in ctx["propositions"]} == {"prop_hmbs_aip"}
    assert ctx["propositions"][0]["related_knowledge"] == ["card_hmbs", "card_aip"]


def test_apply_three_proposal_kinds(kl_home):
    store = Archive(kl_home)
    store.init()
    quote = "HMBS loss impairs heme biosynthesis in hepatocytes."
    push = {
        "push_id": "push_1",
        "checkpoint_id": "card_hmbs",
        "paper_ids": ["p1"],
        "proposals": [
            {
                "proposal_id": "p1",
                "push_id": "push_1",
                "op": "add_proposition_evidence",
                "target_type": "proposition",
                "target_id": "prop_hmbs_aip",
                "diff": {
                    "op": "add",
                    "path": "evidence",
                    "value": {
                        "conclusion": "HMBS 缺失降低血红素合成。",
                        "stance": "supports",
                        "papers": [{"paper_id": "p1", "page": 3, "quote": quote}],
                    },
                },
            },
            {
                "proposal_id": "p2",
                "push_id": "push_1",
                "op": "add_card_ontology",
                "target_type": "card",
                "target_id": "card_hmbs",
                "diff": {"op": "add", "path": "ontology", "value": "HMBS enzyme 2"},
            },
            {
                "proposal_id": "p3",
                "push_id": "push_1",
                "op": "create_proposition",
                "target_type": "none",
                "target_id": None,
                "diff": {
                    "op": "add",
                    "path": "proposition",
                    "value": {
                        "title": "New claim",
                        "review": "A synthetic cross-paper claim.",
                        "related_knowledge": ["card_hmbs", "card_heme_biosynthesis"],
                        "evidence": [
                            {
                                "id": "ev_new_claim_01",
                                "stance": "supports",
                                "conclusion": "First paper supports the claim.",
                                "papers": [{"paper_id": "p1", "page": 1, "quote": quote}],
                            },
                            {
                                "id": "ev_new_claim_02",
                                "stance": "contradicts",
                                "conclusion": "Second paper points the other way.",
                                "papers": [{"paper_id": "p2", "page": 2, "quote": quote}],
                            },
                        ],
                    },
                },
            },
        ],
    }
    store.add_push(push)
    for pid in ("p1", "p2", "p3"):
        store.apply_proposal("push_1", pid)
    prop = store.get_prop("prop_hmbs_aip")
    assert [p["paper_id"] for p in prop["evidence"][0]["papers"]] == ["p1"]
    assert prop["evidence_updates_since_review"] == 1
    assert "HMBS enzyme 2" in store.get_card("card_hmbs")["ontology"]
    created = store.get_prop("prop_new_claim")
    assert len(created["evidence"]) == 2
    assert {e["stance"] for e in created["evidence"]} == {"supports", "contradicts"}


def test_apply_decisions_is_idempotent_and_handles_reject(kl_home, tmp_path):
    store = Archive(kl_home)
    store.init()
    store.add_push(
        {
            "push_id": "push_2",
            "checkpoint_id": "card_hmbs",
            "proposals": [
                {
                    "proposal_id": "px",
                    "push_id": "push_2",
                    "kind": "add_alias",
                    "target_type": "card",
                    "target_id": "card_hmbs",
                    "diff": {"op": "add", "path": "ontology", "value": "alias-x"},
                }
            ],
        }
    )
    pending = tmp_path / "pending"
    pending.mkdir()
    (pending / "px.json").write_text(
        '{"proposal_id":"px","push_id":"push_2","kind":"add_alias",'
        '"target_type":"card","target_id":"card_hmbs",'
        '"diff":{"op":"add","path":"ontology","value":"alias-x"}}',
        encoding="utf-8",
    )
    decisions = tmp_path / "decisions.jsonl"
    decisions.write_text('{"proposal_id":"px","decision":"approve"}\n', encoding="utf-8")
    first = store.apply_decisions(pending, decisions)
    second = store.apply_decisions(pending, decisions)
    assert first["applied"] == ["px"]
    assert second["skipped"] == ["px"]
    assert "alias-x" in store.get_card("card_hmbs")["ontology"]

    decisions.write_text('{"proposal_id":"py","decision":"reject"}\n', encoding="utf-8")
    out = store.apply_decisions(pending, decisions)
    assert out["rejected"] == ["py"]
    assert (kl_home / "archive" / "applied" / "py.json").exists()


def test_card_ids_are_semantic_and_deduped(kl_home):
    store = Archive(kl_home)
    store.init()
    first = store.put_card(
        {
            "label": "Single-cell foundation model",
            "ontology": ["scFM"],
            "annotation": "a",
        }
    )
    assert first["card_id"] == "card_single_cell_foundation_model"
    second = store.put_card(
        {"label": "Single-cell foundation model 2", "ontology": ["scFM2"], "annotation": "b"}
    )
    assert second["card_id"] == "card_single_cell_foundation_model_2"
    with pytest.raises(ArchiveError):
        store.put_card({"id": "kg_bad", "label": "x", "ontology": ["x"], "annotation": "x"})


def test_proposition_requires_two_known_cards(kl_home):
    store = Archive(kl_home)
    store.init()
    with pytest.raises(ArchiveError):
        store.put_prop(
            {
                "id": "prop_one_card",
                "title": "one card",
                "content": "x",
                "related_knowledge": ["card_hmbs"],
                "history": [{"at": "now", "action": "created"}],
            }
        )
    with pytest.raises(ArchiveError):
        store.put_prop(
            {
                "id": "prop_unknown_card",
                "title": "unknown",
                "content": "x",
                "related_knowledge": ["card_hmbs", "card_nope"],
                "history": [{"at": "now", "action": "created"}],
            }
        )


def test_apply_group_cli(kl_home, tmp_path, capsys):
    from archive.cli import main

    card = Archive(kl_home).get_card("card_hmbs")
    item = {
        "id": "q_cli_test",
        "payload": {
            "proposals": [
                {
                    "schema_version": 2,
                    "proposal_id": "p_cli_01",
                    "push_id": "push_cli",
                    "op": "add_card_ontology",
                    "target_type": "card",
                    "target_id": "card_hmbs",
                    "diff": {"op": "add", "path": "ontology", "value": "CLI term"},
                    "before": card["ontology"],
                    "after": card["ontology"] + ["CLI term"],
                    "rationale": "CLI test rationale that is long enough.",
                    "confidence": 0.5,
                    "evidence": {
                        "paper_id": "p1",
                        "page": 1,
                        "quote": "A quote long enough for evidence.",
                    },
                    "status": "draft",
                }
            ]
        },
    }
    path = tmp_path / "item.json"
    path.write_text(json.dumps(item), encoding="utf-8")
    assert main([*cli_locations(kl_home), "apply-group", "--file", str(path)]) == 0
    assert "CLI term" in Archive(kl_home).get_card("card_hmbs")["ontology"]
    assert main([*cli_locations(kl_home), "apply-group", "--file", str(path)]) == 0
    again = [p for p in Archive(kl_home).get_card("card_hmbs")["ontology"] if p == "CLI term"]
    assert len(again) == 1


def test_migration_is_idempotent_and_rolls_back(tmp_path):
    import shutil

    root = Path(__file__).resolve().parents[1] / "fixtures" / "legacy_home"
    home = tmp_path / "home"
    shutil.copytree(root, home)

    first = migrate.migrate(home)
    assert first["status"] == "migrated"
    assert (home / "archive" / "cards" / "card_hmbs.json").exists()
    assert not (home / "horizon.yaml").exists()
    assert not (home / "archive" / "kg").exists()
    assert first["checkpoint_map"]["virtual-cell"] == "card_virtual_cell"
    assert migrate.migrate(home)["status"] == "already-migrated"

    store = Archive(home)
    prop = store.get_prop("prop_virtual_cell_perturbation")
    assert prop["related_knowledge"] == ["card_virtual_cell", "card_perturbation_prediction"]
    assert prop["changelog"][0]["source"]["kind"] == "migration"
    assert prop["evidence"] == []

    backup = Path(first["backup"]).name
    out = migrate.rollback(home, backup)
    assert out["status"] == "rolled-back"
    assert (home / "horizon.yaml").exists()
    assert (home / "archive" / "kg" / "nodes" / "kg_hmbs.json").exists()
    assert not (home / "archive" / "cards").exists()


def test_migrate_v3_converts_history_and_rolls_back(kl_home):
    from archive import migrate

    before = json.loads(
        (kl_home / "archive" / "propositions" / "prop_virtual_cell_perturbation.json").read_text()
    )
    assert "history" in before

    out = migrate.migrate_v3(kl_home)
    assert out["status"] == "migrated"
    assert out["converted"] >= 1
    prop = Archive(kl_home).get_prop("prop_virtual_cell_perturbation")
    assert prop["schema_version"] == 3
    assert "history" not in prop
    assert prop["review"]
    assert prop["evidence"] == []
    assert prop["changelog"][0]["action"] == "created"

    assert migrate.migrate_v3(kl_home)["status"] == "already-migrated"

    backup = Path(out["backup"]).name
    assert migrate.rollback_v3(kl_home, backup)["status"] == "rolled-back"
    restored = json.loads(
        (kl_home / "archive" / "propositions" / "prop_virtual_cell_perturbation.json").read_text()
    )
    assert "history" in restored


def test_v3_conversion_maps_history_evidence_to_one_entry():
    from archive.store import v3_prop_from_v2

    raw = {
        "id": "prop_hmbs_aip",
        "title": "HMBS 缺失导致 AIP",
        "content": "短结论",
        "related_knowledge": ["card_hmbs", "card_aip"],
        "history": [
            {
                "at": "2026-10-20T09:00:00+08:00",
                "action": "created",
                "source": {"kind": "migration", "id": "v1"},
                "before": None,
                "after": {"content": "短结论"},
                "rationale": "初始构建",
                "evidence": [],
            },
            {
                "at": "2026-10-21T09:00:00+08:00",
                "action": "evidence_added",
                "source": {"kind": "push", "id": "p1"},
                "before": {},
                "after": {},
                "rationale": "两篇病例一致显示 HMBS 活性下降导致 AIP。",
                "evidence": [
                    {"paper_id": "pmid1", "push_id": "p1", "page": 3, "quote": "Case report quote one."},
                    {"paper_id": "pmid2", "push_id": "p2", "page": 5, "quote": "Case report quote two."},
                ],
            },
        ],
    }
    prop = v3_prop_from_v2(raw)
    assert prop["review"] == "短结论"
    assert len(prop["evidence"]) == 1
    entry = prop["evidence"][0]
    assert entry["stance"] == "supports"
    assert entry["conclusion"].startswith("两篇病例")
    assert [p["paper_id"] for p in entry["papers"]] == ["pmid1", "pmid2"]
    assert entry["migrated_from_v2"] is True
    assert len(prop["changelog"]) == 1
