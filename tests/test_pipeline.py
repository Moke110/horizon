from pathlib import Path

from common import write_yaml
from editor import model as editor_model
from horizon import model as horizon_model
from retriever import cli as retriever_cli
from writer import model as writer_model


def _candidates(kl_home, fixtures_dir):
    plan = horizon_model.plan(kl_home, day="2026-10-20", days=7)
    return plan, retriever_cli.run(
        plan, "fixture", Path(fixtures_dir), check_window=True
    )


def test_retriever_filters_noise_and_assigns_ids(kl_home, fixtures_dir):
    _, rows = _candidates(kl_home, fixtures_dir)
    assert len(rows) == 4
    ids = {r["paper_id"] for r in rows}
    assert "europepmc_42900001" in ids
    assert "europepmc_42900004" not in ids  # meropenem noise
    assert all(r["paper_id"].startswith(("europepmc_", "arxiv_")) for r in rows)
    assert all("retrieval" in r for r in rows)


def test_editor_ranks_by_relevance_and_preference(kl_home, fixtures_dir, tmp_path):
    _, rows = _candidates(kl_home, fixtures_dir)
    neutral = editor_model.rank(kl_home, rows, day="2026-10-20", limit=10, per_checkpoint=5)
    scores = {r["paper"]["paper_id"]: r["score"] for r in neutral["selected"]}
    assert "europepmc_42900001" in scores
    assert scores["europepmc_42900001"] > 0.4
    assert set(neutral["selected"][0]["breakdown"]) == {
        "relevance",
        "recency",
        "preference",
        "weight",
    }

    model_path = tmp_path / "editor.yaml"
    write_yaml(
        model_path,
        {
            "schema_version": 1,
            "checkpoints": {
                "card_hmbs": {"n": 10, "mean": 1.0},
                "card_perturbation_prediction": {"n": 10, "mean": 0.0},
            },
            "sources": {},
        },
    )
    biased = editor_model.rank(
        kl_home, rows, day="2026-10-20", limit=10, per_checkpoint=5, model_path=model_path
    )
    biased_scores = {r["paper"]["paper_id"]: r["score"] for r in biased["selected"]}
    assert biased_scores["europepmc_42900001"] > scores["europepmc_42900001"]
    assert biased_scores["europepmc_42900003"] < scores["europepmc_42900003"]
    assert biased["selected"][0]["paper"]["paper_id"] == "europepmc_42900001"


def test_writer_produces_four_sections_and_proposals(kl_home, fixtures_dir, tmp_path):
    _, rows = _candidates(kl_home, fixtures_dir)
    selection = editor_model.rank(kl_home, rows, day="2026-10-20", limit=4, per_checkpoint=2)
    result = writer_model.write(selection, kl_home, tmp_path / "outbox", "2026-10-20")
    assert result["count"] == 4
    hmbs = next(p for p in result["pushes"] if p["checkpoint_id"] == "card_hmbs")
    assert set(hmbs["sections"]) == {"did", "found", "why", "keep"}
    assert "为什么重要" in hmbs["body_md"]
    assert hmbs["title"].startswith("HMBS")
    ops = {p["op"] for p in hmbs["proposals"]}
    assert "add_proposition_evidence" in ops
    assert all(p["status"] == "draft" for p in hmbs["proposals"])
    assert (tmp_path / "outbox" / "pushes" / f"{hmbs['push_id']}.json").exists()
