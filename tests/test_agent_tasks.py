import json

from editor import model as editor_model
from editor import tasks as editor_tasks
from horizon import model as horizon_model
from retriever import cli as retriever_cli
from writer import model as writer_model
from writer import tasks as writer_tasks

def _pipeline(kl_home, fixtures_dir):
    plan = horizon_model.plan(kl_home, day="2026-10-20", days=7)
    rows = retriever_cli.run(plan, "fixture", fixtures_dir, check_window=True)
    selection = editor_model.rank(kl_home, rows, day="2026-10-20", limit=4, per_checkpoint=2)
    return rows, selection


def test_editor_task_contains_knowledge_context(kl_home, fixtures_dir):
    rows, _ = _pipeline(kl_home, fixtures_dir)
    task = editor_tasks.build_task(kl_home, rows, "2026-10-20", limit=4, per_checkpoint=2)
    assert task["kind"] == "editor-task"
    assert len(task["candidates"]) == 4
    hmbs = task["checkpoints"]["card_hmbs"]
    assert hmbs["card"]["id"] == "card_hmbs"
    assert any(p["prop_id"] == "prop_hmbs_aip" for p in hmbs["propositions"])
    assert "annotation" in hmbs


def test_editor_check_accepts_and_rejects(kl_home, fixtures_dir):
    rows, selection = _pipeline(kl_home, fixtures_dir)
    task = editor_tasks.build_task(kl_home, rows, "2026-10-20", limit=4, per_checkpoint=2)
    assert editor_tasks.validate(task, selection)["ok"] is True

    bad = json.loads(json.dumps(selection))
    bad["selected"][0]["paper"]["paper_id"] = "made_up_999"
    result = editor_tasks.validate(task, bad)
    assert result["ok"] is False
    assert any("not in candidates" in e for e in result["errors"])

    dup = json.loads(json.dumps(selection))
    dup["selected"].append(json.loads(json.dumps(dup["selected"][0])))
    result = editor_tasks.validate(task, dup)
    assert result["ok"] is False
    assert any("duplicate" in e for e in result["errors"])

    no_reason = json.loads(json.dumps(selection))
    no_reason["selected"][0]["rationale"] = ""
    result = editor_tasks.validate(task, no_reason)
    assert result["ok"] is False


def test_writer_task_and_validation_roundtrip(kl_home, fixtures_dir, tmp_path):
    from archive.store import Archive

    store = Archive(kl_home)
    store.init()
    store.add_paper(
        {
            "source": "europepmc",
            "source_id": "42900001",
            "title": "HMBS loss",
            "abstract": "We show HMBS loss impairs heme biosynthesis.",
            "checkpoint_id": "card_hmbs",
            "published": "2026-10-16",
        }
    )
    _, selection = _pipeline(kl_home, fixtures_dir)
    task = writer_tasks.build_task(kl_home, selection, "2026-10-20")
    assert task["kind"] == "writer-task"
    assert len(task["items"]) == len(selection["selected"])
    assert task["items"][0]["context"]["card"]["id"]
    hmbs_item = next(i for i in task["items"] if i["checkpoint_id"] == "card_hmbs")
    assert hmbs_item["context"]["recent_papers"]
    assert "paper_id" in hmbs_item["context"]["recent_papers"][0]

    outbox = tmp_path / "outbox"
    writer_model.write(selection, kl_home, outbox, "2026-10-20")
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is True
    assert result["pushes"] == len(selection["selected"])

    target = sorted((outbox / "pushes").glob("*.json"))[0]
    push = json.loads(target.read_text())
    push["sections"]["why"] = ""
    target.write_text(json.dumps(push, ensure_ascii=False))
    bad = writer_tasks.validate(task, outbox)
    assert bad["ok"] is False
    assert any("sections.why" in e for e in bad["errors"])


def test_writer_rejects_unknown_proposal_target(kl_home, fixtures_dir, tmp_path):
    _, selection = _pipeline(kl_home, fixtures_dir)
    task = writer_tasks.build_task(kl_home, selection, "2026-10-20")
    outbox = tmp_path / "outbox"
    writer_model.write(selection, kl_home, outbox, "2026-10-20")
    target = sorted((outbox / "pushes").glob("*.json"))[0]
    push = json.loads(target.read_text())
    push["proposals"] = [
        {
            "schema_version": 2,
            "proposal_id": "p_test_01",
            "push_id": push["push_id"],
            "op": "add_card_ontology",
            "target_type": "card",
            "target_id": "card_missing",
            "diff": {"op": "add", "path": "ontology", "value": "x"},
            "before": [],
            "after": ["x"],
            "rationale": "testing an unknown card target",
            "confidence": 0.5,
            "evidence": {
                "paper_id": push["paper_ids"][0],
                "page": 1,
                "quote": "HMBS loss impairs heme biosynthesis",
            },
            "status": "draft",
        }
    ]
    target.write_text(json.dumps(push, ensure_ascii=False))
    result = writer_tasks.validate(task, outbox)
    assert result["ok"] is False
    assert any("unknown card" in e for e in result["errors"])


def test_editor_task_skips_already_pushed_papers(kl_home, fixtures_dir):
    rows, _ = _pipeline(kl_home, fixtures_dir)
    pushed_id = rows[0]["paper_id"]
    push_dir = kl_home / "archive" / "pushes"
    push_dir.mkdir(parents=True, exist_ok=True)
    (push_dir / "2026-10-20_test_01.json").write_text(
        json.dumps(
            {"schema_version": 2, "push_id": "2026-10-20_test_01", "paper_ids": [pushed_id]}
        ),
        encoding="utf-8",
    )

    task = editor_tasks.build_task(kl_home, rows, "2026-10-20", limit=4, per_checkpoint=2)
    candidate_ids = {row["paper_id"] for row in task["candidates"]}
    assert pushed_id not in candidate_ids
    assert len(candidate_ids) == len(rows) - 1


def test_temporary_smoke_cards_are_excluded_from_editor_and_writer_context(
    kl_home, fixtures_dir
):
    temp = kl_home / "archive" / "cards" / "card_smoke_acceptance.json"
    temp.write_text(
        json.dumps(
            {
                "id": "card_smoke_acceptance",
                "label": "[临时验收] 虚拟细胞",
                "ontology": ["虚拟细胞"],
                "annotation": "⛔ 验收内容 ⚠️ 不得进入正式上下文",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rows, selection = _pipeline(kl_home, fixtures_dir)
    editor_task = editor_tasks.build_task(
        kl_home, rows, "2026-10-20", limit=4, per_checkpoint=2
    )
    assert "card_smoke_acceptance" not in editor_task["checkpoints"]
    assert "card_hmbs" in editor_task["checkpoints"]

    writer_task = writer_tasks.build_task(kl_home, selection, "2026-10-20")
    card_ids = {
        card["id"]
        for item in writer_task["items"]
        for card in item["context"]["cards"]
    }
    assert "card_smoke_acceptance" not in card_ids
    assert "card_hmbs" in card_ids
