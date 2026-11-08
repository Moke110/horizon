"""Offline tests for ``/horizon-update-kb`` per-item review (issue #90, ADR 0009 §8).

The console consumes the candidate area written by the read side (#89), shows one
candidate at a time and reads a single key:

- Enter accepts, Backspace discards, Tab keeps but marks ``Under review``;
- ``q``/Esc aborts: every already-decided item is applied immediately and the
  undecided ones are shown again next time;
- a non-TTY stdin refuses to enter the review with the console hint;
- a discarded candidate never enters the knowledge base but stays in the
  candidate area and the decision journal;
- when every item is decided the run writes a batch confirmation (hash +
  decision summary), syncs the Base, restores the service and starts one real
  first-push drill.

Only the public seam (``hostrun.update_kb_review.run`` plus the console
dispatcher) is exercised.  The key source, the service, the Base sync and the
clock are injected, so no real TTY, Feishu tenant, ``dsh`` or network is touched.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from archive.store import Archive, is_under_review
from common import read_json
from hostrun import console, update_kb, update_kb_review

# ------------------------------------------------------------------ fixtures
CARD = {
    "id": "card_ok",
    "label": "标签",
    "ontology": ["主题"],
    "annotation": "注释",
}
PROP = {
    "id": "prop_ok",
    "title": "标题",
    "review": "这是一段综述内容",
    "related_knowledge": ["card_ok", "card_two"],
    "evidence": [
        {
            "id": "ev_ok_01",
            "stance": "supports",
            "conclusion": "结论",
            "papers": [
                {
                    "paper_id": "pdf_demo",
                    "page": 1,
                    "quote": "一段至少八个字符的引文",
                }
            ],
        }
    ],
}


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "code" / ".horizon"
    (home / "install").mkdir(parents=True)
    (home / "archive").mkdir(parents=True)
    return home


def _seed(home: Path, *, cards=None, propositions=None) -> Path:
    """Write the candidate area exactly as the read side (#89) leaves it."""
    update_kb.write_reports(
        home,
        candidates={
            "cards": list(cards or []),
            "propositions": list(propositions or []),
            "duplicates": 0,
        },
        unread=[],
        source=str(home.parent / "inbox"),
    )
    return update_kb.candidates_path(home)


def _keys(*values):
    sequence = iter(values)

    def read():
        try:
            return next(sequence)
        except StopIteration:  # pragma: no cover - a test bug, not product behaviour
            raise AssertionError("no more injected keys")

    return read


class FakeService:
    def __init__(self, *, push=None):
        self.events: list[object] = []
        self.push_result = push or {"ok": True, "action": "push", "started": True}

    def start(self) -> dict:
        self.events.append("start")
        return {"ok": True, "action": "start", "service": "running"}

    def request_push(self, trigger: str = "manual") -> dict:
        self.events.append(("push", trigger))
        return dict(self.push_result)


def _sync(ok=True):
    calls: list[Path] = []

    def sync(runtime):
        calls.append(Path(runtime))
        return {"ok": ok, "dry_run": False, "created": {"cards": 0}}

    sync.calls = calls  # type: ignore[attr-defined]
    return sync


def _run(home, keys, *, service=None, sync=None, isatty=True, out=None, **kwargs):
    output = out or io.StringIO()
    result = update_kb_review.run(
        home,
        key_reader=keys,
        out=output,
        isatty=lambda: isatty,
        sync=sync or _sync(),
        service=service or FakeService(),
        clock=lambda: "2026-10-30T10:00:00+08:00",
        **kwargs,
    )
    return result, output


# ------------------------------------------------------------------- Enter
def test_enter_accepts_a_candidate_into_the_knowledge_base(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD])
    service = FakeService()

    result, _ = _run(home, _keys("\r"), service=service)

    assert result["ok"] is True
    assert result["completed"] is True
    stored = Archive(home).get_card("card_ok")
    assert stored["label"] == "标签"
    assert is_under_review(stored) is False
    assert result["counts"]["accepted"] == 1
    assert result["written"]["cards"] == 1
    # Completion restores the service and runs one real first-push drill.
    assert service.events == ["start", ("push", "first")]


def test_each_decision_result_is_echoed_with_its_progress(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD, {**CARD, "id": "card_two", "label": "标签二"}])

    _, output = _run(home, _keys("\r", "\x7f"))
    text = output.getvalue()

    assert "第 1 / 2" in text
    assert "第 2 / 2" in text
    assert "已接受" in text
    assert "已丢弃" in text


# --------------------------------------------------------------- Backspace
def test_backspace_discards_without_writing_but_keeps_the_trail(tmp_path):
    home = _home(tmp_path)
    candidates = _seed(home, cards=[CARD])

    result, _ = _run(home, _keys("\x7f"))

    assert result["counts"]["discarded"] == 1
    with pytest.raises(Exception):
        Archive(home).get_card("card_ok")
    # The candidate stays in the candidate area ...
    assert json.loads(candidates.read_text(encoding="utf-8"))["cards"][0]["id"] == "card_ok"
    # ... and the discarded decision stays in the journal with the item snapshot.
    decisions = update_kb_review.read_decisions(home)
    assert len(decisions) == 1
    assert decisions[0]["key"] == "card:card_ok"
    assert decisions[0]["decision"] == "discarded"
    assert decisions[0]["record"]["label"] == "标签"


# --------------------------------------------------------------------- Tab
def test_tab_keeps_a_card_but_marks_it_under_review(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD])

    result, _ = _run(home, _keys("\t"))

    stored = Archive(home).get_card("card_ok")
    assert is_under_review(stored) is True
    assert result["counts"]["kept"] == 1
    # Still counted locally, just excluded from formal retrieval.
    assert Archive(home).list_cards(include_under_review=False) == []


def test_a_kept_proposition_records_no_review_changelog(tmp_path):
    home = _home(tmp_path)
    _seed(
        home,
        cards=[CARD, {**CARD, "id": "card_two", "label": "标签二"}],
        propositions=[PROP],
    )

    _, _ = _run(home, _keys("\r", "\r", "\t"))

    prop = Archive(home).get_prop("prop_ok")
    assert is_under_review(prop) is True
    assert [entry["action"] for entry in prop["changelog"]] == ["created"]


# ----------------------------------------------------------- abort/resume
def test_q_aborts_applies_decided_items_and_leaves_the_rest(tmp_path):
    home = _home(tmp_path)
    _seed(
        home,
        cards=[
            CARD,
            {**CARD, "id": "card_two", "label": "标签二"},
            {**CARD, "id": "card_three", "label": "标签三"},
        ],
    )
    service = FakeService()

    result, output = _run(home, _keys("\r", "\x7f", "q"), service=service)

    assert result["aborted"] is True
    assert result["completed"] is False
    assert result["counts"] == {
        "total": 3,
        "accepted": 1,
        "discarded": 1,
        "kept": 0,
        "remaining": 1,
    }
    # Decided items take effect immediately; the undecided one does not.
    assert Archive(home).get_card("card_ok")["label"] == "标签"
    assert not (home / "archive" / "cards" / "card_three.json").exists()
    # The review restores the service it inherited paused, but runs no
    # completion drill while the review is incomplete.
    assert service.events == ["start"]
    assert "中止" in output.getvalue()
    # An aborted review still records the batch it did decide.
    payload = read_json(update_kb_review.confirmation_path(home))
    assert payload["aborted"] is True
    assert payload["counts"]["remaining"] == 1


def test_escaping_aborts_but_keeps_the_already_decided_item(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD, {**CARD, "id": "card_two", "label": "标签二"}])

    result, _ = _run(home, _keys("\r", "\x1b"))

    assert result["aborted"] is True
    assert Archive(home).get_card("card_ok")["label"] == "标签"
    assert result["counts"]["remaining"] == 1


def test_the_next_run_only_shows_the_undecided_items(tmp_path):
    home = _home(tmp_path)
    _seed(
        home,
        cards=[
            CARD,
            {**CARD, "id": "card_two", "label": "标签二"},
            {**CARD, "id": "card_three", "label": "标签三"},
        ],
    )
    _run(home, _keys("\r", "q"))

    result, output = _run(home, _keys("\r", "\r"))
    text = output.getvalue()
    assert "第 1 / 2" in text
    assert "第 2 / 2" in text
    assert result["completed"] is True
    assert result["counts"]["accepted"] == 3
    assert result["counts"]["remaining"] == 0
    assert Archive(home).get_card("card_three")["label"] == "标签三"


def test_a_decision_is_persisted_before_the_next_item_is_shown(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD, {**CARD, "id": "card_two", "label": "标签二"}])
    seen: list[int] = []

    def key_reader():
        # Before the second key is read, the first decision must already be on disk.
        seen.append(len(update_kb_review.read_decisions(home)))
        return "\r"

    update_kb_review.run(
        home,
        key_reader=key_reader,
        out=io.StringIO(),
        isatty=lambda: True,
        sync=_sync(),
        service=FakeService(),
        clock=lambda: "2026-10-30T10:00:00+08:00",
    )

    assert seen[:2] == [0, 1]


# ------------------------------------------------------------------ refusal
def test_a_non_tty_refuses_to_enter_the_review_and_writes_nothing(tmp_path):
    home = _home(tmp_path)
    candidates = _seed(home, cards=[CARD])
    before = candidates.read_text(encoding="utf-8")

    result, _ = _run(home, _keys("\r"), isatty=False)

    assert result["ok"] is False
    assert result["reason"] == "not_a_tty"
    assert "请在控制台执行" in result["text"]
    assert update_kb_review.read_decisions(home) == []
    assert candidates.read_text(encoding="utf-8") == before
    assert not (home / "archive" / "cards" / "card_ok.json").exists()


def test_no_candidate_area_reports_rather_than_inventing_items(tmp_path):
    home = _home(tmp_path)

    result, _ = _run(home, _keys())

    assert result["ok"] is False
    assert result["reason"] == "no_candidates"
    assert update_kb_review.read_decisions(home) == []


# ----------------------------------------------------- confirmation/sync
def test_completion_writes_a_batch_confirmation_with_hash_and_summary(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD, {**CARD, "id": "card_two", "label": "标签二"}])

    result, _ = _run(home, _keys("\r", "\t"))

    payload = read_json(update_kb_review.confirmation_path(home))
    assert payload["batch_hash"] == result["batch_hash"]
    assert payload["batch_hash"].startswith("review_")
    assert payload["counts"] == {
        "total": 2,
        "accepted": 1,
        "discarded": 0,
        "kept": 1,
        "remaining": 0,
    }
    assert {(row["key"], row["decision"]) for row in payload["decisions"]} == {
        ("card:card_ok", "accepted"),
        ("card:card_two", "kept"),
    }


def test_base_sync_failure_is_reported_but_the_local_write_stays(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD])

    def failing_sync(runtime):
        raise RuntimeError("tenant unreachable")

    result, _ = _run(home, _keys("\r"), sync=failing_sync)

    assert Archive(home).get_card("card_ok")["label"] == "标签"
    assert result["base_sync"]["ok"] is False
    assert "tenant unreachable" in result["base_sync"]["reason"]
    assert "Base 同步：失败" in result["text"]


def test_a_failed_first_push_drill_is_reported_honestly(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD])
    service = FakeService(push={"ok": False, "reason": "service_paused"})

    result, _ = _run(home, _keys("\r"), service=service)

    assert result["first_push"]["ok"] is False
    assert result["first_push"]["reason"] == "service_paused"
    assert "首次推送" in result["text"]


def test_a_service_paused_before_the_read_stays_paused_and_reports_why(tmp_path):
    home = _home(tmp_path)
    _seed(home, cards=[CARD])
    console.write_state(home, paused=True, service="paused")
    service = FakeService(push={"ok": False, "reason": "service_paused"})

    result, _ = _run(home, _keys("\r"), service=service)

    assert service.events == [("push", "first")]
    assert result["restored"] is False
    assert result["first_push"]["ok"] is False


# ------------------------------------------------------- existing knowledge
def test_an_existing_card_with_the_same_id_is_not_overwritten(tmp_path):
    home = _home(tmp_path)
    store = Archive(home)
    original = {"id": "card_ok", "label": "原有标签", "ontology": ["旧"], "annotation": "旧注释"}
    store.put_card(original)
    _seed(home, cards=[CARD])

    result, _ = _run(home, _keys("\r"))

    assert store.get_card("card_ok")["annotation"] == "旧注释"
    assert result["written"]["cards"] == 0
    assert result["written"]["failed"][0]["reason"] == "pre_existing"


# --------------------------------------------------------- console dispatch
def test_dispatch_reviews_after_a_completed_read_and_combines_the_text(tmp_path):
    home = _home(tmp_path)
    calls: list[dict] = []

    def fake_read(runtime, **kwargs):
        return {"ok": True, "action": "update_kb", "reason": "done", "text": "读取完成"}

    def fake_review(runtime, **kwargs):
        calls.append({"runtime": Path(runtime), **kwargs})
        return {"ok": True, "completed": True, "text": "审阅完成"}

    result = console.dispatch(
        home, "/horizon-update-kb", update_kb=fake_read, review=fake_review
    )

    assert calls and calls[0]["runtime"] == home
    assert result["ok"] is True
    assert "读取完成" in result["text"]
    assert "审阅完成" in result["text"]
    assert result["review"]["completed"] is True


def test_dispatch_does_not_review_an_interrupted_read(tmp_path):
    home = _home(tmp_path)
    calls: list[dict] = []

    def fake_read(runtime, **kwargs):
        return {
            "ok": False,
            "action": "update_kb",
            "reason": "interrupted",
            "pending_shards": 2,
            "text": "读取中断",
        }

    def fake_review(runtime, **kwargs):  # pragma: no cover - must not be called
        calls.append(kwargs)
        return {"ok": True, "text": "不应审阅"}

    result = console.dispatch(
        home, "/horizon-update-kb", update_kb=fake_read, review=fake_review
    )

    assert calls == []
    assert result["ok"] is False
