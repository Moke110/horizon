"""Offline tests for ``/horizon-update-kb`` read-side (issue #89, ADR 0009 §8).

The Console command pauses the service, copies a chosen directory into the
archive's ``originals/`` (md5 verified, source untouched), shards whole files by
a character budget, runs at most two read-only agent runs, merges their candidate
cards/propositions into the candidate area and reports every file it could not
read.  Repeating it is incremental: already-registered files are skipped and
proven by md5.

These tests drive the public seam only: ``hostrun.update_kb.run(...)`` plus the
console dispatcher line.  The uploader and the agent runner are injected, so no
real Feishu, no real ``dsh``, no network and no TTY are touched.  A live
credentialed agent run and a real Lark upload stay ``untested`` in the report.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from hostrun import archive_index, console, update_kb


# ------------------------------------------------------------------ fixtures
def _runtime(tmp_path: Path) -> Path:
    home = tmp_path / "code" / ".horizon"
    (home / "install").mkdir(parents=True)
    return home


def _archive(tmp_path: Path) -> Path:
    archive = tmp_path / "papers"
    (archive / "originals").mkdir(parents=True)
    return archive


def _source(tmp_path: Path, files: dict[str, str]) -> Path:
    source = tmp_path / "inbox"
    source.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return source


class FakeService:
    def __init__(self, events: list[str]):
        self.events = events

    def pause(self) -> dict:
        self.events.append("pause")
        return {"ok": True, "action": "stop", "paused": True}

    def start(self) -> dict:
        self.events.append("start")
        return {"ok": True, "action": "start", "paused": False}


def _success_agent(cards=None):
    calls: list[dict] = []

    def run(home, task, **kwargs):
        calls.append({"home": Path(home), "task": task, **kwargs})
        payload = {
            "cards": cards
            if cards is not None
            else [
                {
                    "id": "card_ok",
                    "label": "标签",
                    "ontology": "主题",
                    "annotation": "注释",
                }
            ],
            "propositions": [],
        }
        return {"ok": True, "final_text": json.dumps(payload, ensure_ascii=False)}

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _fake_upload():
    calls: list[dict] = []

    def upload(runtime, paper_id, path):
        calls.append({"runtime": Path(runtime), "paper_id": paper_id, "path": Path(path)})
        return {"ok": True, "paper_id": paper_id, "status": "uploaded"}

    upload.calls = calls  # type: ignore[attr-defined]
    return upload


def _run(tmp_path, source, *, archive=None, agent=None, upload=None, **kwargs):
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path) if archive is None else archive
    result = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=FakeService([]),
        upload=upload or _fake_upload(),
        run_agent=agent or _success_agent(),
        **kwargs,
    )
    return home, paper_dir, result


# ------------------------------------------------------------ copy into archive
def test_files_are_copied_with_md5_and_the_source_is_untouched(tmp_path):
    source = _source(tmp_path, {"a.txt": "alpha", "b.md": "beta"})
    before = {p.name: p.read_bytes() for p in source.iterdir()}

    home, paper_dir, result = _run(tmp_path, source)

    assert result["ok"] is True
    assert result["unread"] == []
    copied = {Path(item["archive_path"]).name: item for item in result["copied"]}
    assert set(copied) == {"a.txt", "b.md"}
    for name, row in copied.items():
        stored = Path(row["archive_path"])
        assert stored.is_file()
        assert stored.read_bytes() == (source / name).read_bytes()
        assert archive_index.file_md5(stored) == row["md5"]
    # The source directory is not modified and keeps exactly its two files.
    assert {p.name: p.read_bytes() for p in source.iterdir()} == before
    # Only the two allowed material roots may appear in the archive.
    assert [p.name for p in paper_dir.iterdir()] == ["originals"]
    # Working files/candidates live in the code directory, never the archive.
    assert (home / "update-kb" / "candidates" / "candidates.json").is_file()


def test_second_run_skips_registered_files_and_reads_only_new_ones(tmp_path):
    source = _source(tmp_path, {"a.txt": "alpha"})
    home, paper_dir, first = _run(tmp_path, source)
    assert first["unread"] == []
    first_agent_calls = len(first["shards"])

    (source / "b.txt").write_text("beta", encoding="utf-8")
    agent = _success_agent()
    second = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=FakeService([]),
        upload=_fake_upload(),
        run_agent=agent,
    )

    skipped = {Path(item["source"]).name for item in second["skipped_registered"]}
    assert skipped == {"a.txt"}
    assert [Path(item["source"]).name for item in second["copied"]] == ["b.txt"]
    assert second["skipped_registered"][0]["md5"] == archive_index.file_md5(
        source / "a.txt"
    )
    # Only the new file reaches an agent run.
    assert len(agent.calls) == 1
    assert "b.txt" in agent.calls[0]["task"]
    assert "a.txt" not in agent.calls[0]["task"]
    assert first_agent_calls >= 1


def test_a_different_file_at_the_destination_is_never_overwritten(tmp_path):
    source = _source(tmp_path, {"a.txt": "alpha"})
    paper_dir = _archive(tmp_path)
    target = paper_dir / "originals" / "doc_a" / "a.txt"
    target.parent.mkdir(parents=True)
    target.write_text("someone else's copy", encoding="utf-8")

    home = _runtime(tmp_path)
    agent = _success_agent()
    result = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=FakeService([]),
        upload=_fake_upload(),
        run_agent=agent,
    )

    assert target.read_text(encoding="utf-8") == "someone else's copy"
    assert result["ok"] is True
    assert result["copied"] == []
    assert any("未读取" in row["reason"] for row in result["unread"])
    assert agent.calls == []


# ------------------------------------------------------------------- budget
def test_whole_files_are_packed_up_to_the_batch_budget(tmp_path):
    source = _source(tmp_path, {"a.txt": "a" * 40, "b.txt": "b" * 40, "c.txt": "c" * 40})
    _, _, result = _run(tmp_path, source, window_chars=1000)

    assert [shard["chars"] for shard in result["shards"]] == [80, 40]
    assert [[Path(path).name for path in shard["files"]] for shard in result["shards"]] == [
        ["a.txt", "b.txt"],
        ["c.txt"],
    ]


def test_one_file_may_exceed_the_batch_budget_on_its_own(tmp_path):
    source = _source(tmp_path, {"a_big.txt": "x" * 300, "b_small.txt": "y" * 20})
    _, _, result = _run(tmp_path, source, window_chars=1000)

    assert [shard["chars"] for shard in result["shards"]] == [300, 20]
    assert [len(shard["files"]) for shard in result["shards"]] == [1, 1]


def test_a_file_over_half_the_window_is_reported_unread_with_the_limit_reason(tmp_path):
    source = _source(tmp_path, {"huge.txt": "z" * 600})
    agent = _success_agent()
    _, _, result = _run(tmp_path, source, window_chars=1000, agent=agent)

    assert result["unread"][0]["reason"] == update_kb.UNREAD_LIMIT_REASON
    assert result["unread"][0]["reason"] == "未读取：超出读取上限"
    assert result["shards"] == []
    assert agent.calls == []
    assert "未读取清单" in result["unread_text"]


# -------------------------------------------------------------- agent orchestration
def test_at_most_two_agent_runs_are_concurrent(tmp_path):
    source = _source(tmp_path, {f"{name}.txt": name * 10 for name in "abc"})
    state = {"active": 0, "max_active": 0, "starts": 0}
    condition = threading.Condition()

    def runner(home, task, **kwargs):
        with condition:
            state["active"] += 1
            state["starts"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            condition.notify_all()
            # A correct two-slot scheduler releases this wait immediately; a
            # one-slot scheduler times out and the assertion below fails.
            if state["active"] < 2:
                condition.wait_for(lambda: state["active"] >= 2, timeout=2.0)
        try:
            return {
                "ok": True,
                "final_text": json.dumps({"cards": [], "propositions": []}),
            }
        finally:
            with condition:
                state["active"] -= 1
                condition.notify_all()

    _, _, result = _run(tmp_path, source, window_chars=100, agent=runner)

    assert len(result["shards"]) == 3
    assert state["starts"] == 3
    assert state["max_active"] == 2


def test_a_failed_shard_is_retried_once_then_skipped_and_reported(tmp_path):
    source = _source(tmp_path, {"bad.txt": "bad"})
    calls: list[str] = []

    def failing(home, task, **kwargs):
        calls.append(task)
        return {"ok": False, "reason": "child_failed"}

    _, _, result = _run(tmp_path, source, agent=failing)

    assert len(calls) == 2
    assert result["unread"][0]["reason"] == update_kb.UNREAD_RETRY_REASON
    assert "已重试一次" in result["unread"][0]["reason"]
    assert result["cards"] == 0


def test_a_second_attempt_that_succeeds_is_accepted(tmp_path):
    source = _source(tmp_path, {"flaky.txt": "flaky"})
    calls: list[str] = []

    def flaky(home, task, **kwargs):
        calls.append(task)
        if len(calls) == 1:
            return {"ok": False, "reason": "child_failed"}
        return {
            "ok": True,
            "final_text": json.dumps(
                {"cards": [{"id": "card_ok", "label": "L", "ontology": "O", "annotation": "A"}]}
            ),
        }

    _, _, result = _run(tmp_path, source, agent=flaky)

    assert len(calls) == 2
    assert result["unread"] == []
    assert result["cards"] == 1


def test_sub_runs_only_write_candidates_and_never_the_knowledge_base(tmp_path):
    source = _source(tmp_path, {"a.txt": "alpha"})
    agent = _success_agent()
    home, _, result = _run(tmp_path, source, agent=agent)

    assert "不要写入知识库" in agent.calls[0]["task"]
    assert agent.calls[0]["lease"] is False
    assert agent.calls[0]["single_flight"] is False
    assert not (home / "archive" / "cards").exists()
    assert not (home / "archive" / "propositions").exists()
    saved = json.loads((home / "update-kb" / "candidates" / "candidates.json").read_text("utf-8"))
    assert saved["cards"] == [{"id": "card_ok", "label": "标签", "ontology": "主题", "annotation": "注释"}]
    assert result["cards"] == 1


def test_candidates_from_several_shards_are_merged_and_deduplicated(tmp_path):
    source = _source(tmp_path, {"a.txt": "a" * 10, "b.txt": "b" * 10})
    _, _, result = _run(tmp_path, source, window_chars=100)

    assert len(result["shards"]) == 2
    assert result["cards"] == 1
    assert result["duplicate_candidates"] == 1


# ------------------------------------------------------- pause and restore
def test_the_service_is_paused_before_reading_and_restored_afterwards(tmp_path):
    source = _source(tmp_path, {"a.txt": "alpha"})
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    events: list[str] = []
    service = FakeService(events)
    order: list[str] = []

    def agent(home, task, **kwargs):
        order.append("agent")
        assert events == ["pause"]
        return {"ok": True, "final_text": json.dumps({"cards": [], "propositions": []})}

    result = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=service,
        upload=_fake_upload(),
        run_agent=agent,
    )

    assert order == ["agent"]
    assert events == ["pause", "start"]
    assert result["paused_before"] is False
    assert result["restored"] is True


def test_a_paused_service_stays_paused_after_the_read(tmp_path):
    source = _source(tmp_path, {"a.txt": "alpha"})
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    console.write_state(home, paused=True)
    events: list[str] = []

    result = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=FakeService(events),
        upload=_fake_upload(),
        run_agent=_success_agent(),
    )

    assert result["paused_before"] is True
    assert result["restored"] is True
    assert "start" not in events
    assert console.read_state(home)["paused"] is True


def test_cancelling_the_directory_picker_restores_the_service(tmp_path):
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    events: list[str] = []

    result = update_kb.run(
        home,
        archive=paper_dir,
        service=FakeService(events),
        choose_directory_fn=lambda: None,
        upload=_fake_upload(),
        run_agent=_success_agent(),
    )

    assert result["reason"] == "cancelled"
    assert result["copied"] == []
    assert events == ["pause", "start"]


def test_the_pause_covers_the_per_item_review_until_it_restores(tmp_path):
    """S1: the scheduler must not be live during the human-paced review (US25)."""
    source = _source(tmp_path, {"a.txt": "alpha"})
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    events: list[str] = []
    seen: dict = {}

    class StatefulService(FakeService):
        """The real Service persists the pause; mirror that for the assertion."""

        def pause(self) -> dict:
            console.write_state(home, service="paused", paused=True)
            return super().pause()

        def start(self) -> dict:
            console.write_state(home, service="running", paused=False)
            return super().start()

    service = StatefulService(events)

    def review(runtime, *, service=None):
        seen["paused"] = console.read_state(runtime)["paused"]
        seen["events"] = list(events)
        service.start()
        return {
            "ok": True,
            "action": "review_kb",
            "reason": "completed",
            "text": "审阅完成",
            "restored": True,
        }

    def runner(runtime, **kwargs):
        kwargs.pop("source_dir", None)
        return update_kb.run(
            runtime,
            source_dir=source,
            archive=paper_dir,
            upload=_fake_upload(),
            run_agent=_success_agent(),
            **kwargs,
        )

    result = console.dispatch(
        home,
        "/horizon-update-kb",
        service=service,
        update_kb=runner,
        review=review,
    )

    assert result["ok"] is True, result.get("text")
    # The service was still paused when the review started, and stayed paused
    # until the review itself restored it.
    assert seen["paused"] is True
    assert seen["events"] == ["pause"]
    assert events == ["pause", "start"]


def test_a_cancelled_read_releases_the_deferred_pause(tmp_path):
    """S1 guard: if no review runs, the console must still restore the service."""
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    events: list[str] = []

    class StatefulService(FakeService):
        def pause(self) -> dict:
            console.write_state(home, service="paused", paused=True)
            return super().pause()

        def start(self) -> dict:
            console.write_state(home, service="running", paused=False)
            return super().start()

    service = StatefulService(events)

    def runner(runtime, **kwargs):
        kwargs.pop("source_dir", None)
        return update_kb.run(
            runtime,
            archive=paper_dir,
            choose_directory_fn=lambda: None,
            upload=_fake_upload(),
            run_agent=_success_agent(),
            **kwargs,
        )

    def review(runtime, *, service=None):  # pragma: no cover - must not run
        raise AssertionError("a cancelled read must not enter the review")

    result = console.dispatch(
        home,
        "/horizon-update-kb",
        service=service,
        update_kb=runner,
        review=review,
    )

    assert result["ok"] is False
    assert result["reason"] == "cancelled"
    assert events == ["pause", "start"]
    assert console.read_state(home)["paused"] is False


def test_a_directory_inside_the_archive_is_refused(tmp_path):
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    source = paper_dir / "originals" / "inbox"
    source.mkdir(parents=True)
    (source / "a.txt").write_text("alpha", encoding="utf-8")

    result = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=FakeService([]),
        upload=_fake_upload(),
        run_agent=_success_agent(),
    )

    assert result["reason"] == "source_inside_archive"
    assert result["copied"] == []


def test_a_directory_containing_the_archive_is_refused(tmp_path):
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    parent = paper_dir.parent
    (parent / "a.txt").write_text("alpha", encoding="utf-8")

    result = update_kb.run(
        home,
        source_dir=parent,
        archive=paper_dir,
        service=FakeService([]),
        upload=_fake_upload(),
        run_agent=_success_agent(),
    )

    assert result["reason"] == "source_contains_archive"
    assert list((paper_dir / "originals").iterdir()) == []


# ---------------------------------------------------------- interruption/resume
def test_an_interrupted_run_leaves_pending_shards_and_the_next_run_resumes_them(tmp_path):
    source = _source(tmp_path, {"a.txt": "a" * 10, "b.txt": "b" * 10, "c.txt": "c" * 10})
    home = _runtime(tmp_path)
    paper_dir = _archive(tmp_path)
    stop = {"value": False}

    def stopping_agent(home, task, **kwargs):
        stop["value"] = True
        return {"ok": True, "final_text": json.dumps({"cards": [], "propositions": []})}

    first = update_kb.run(
        home,
        source_dir=source,
        archive=paper_dir,
        service=FakeService([]),
        window_chars=100,
        upload=_fake_upload(),
        run_agent=stopping_agent,
        should_stop=lambda: stop["value"],
    )

    assert first["reason"] == "interrupted"
    assert first["pending_shards"] >= 1
    state = update_kb.read_state(home)
    assert state["phase"] == "interrupted"
    pending = update_kb.pending_shards(state)
    assert len(pending) == first["pending_shards"]

    resumed_agent = _success_agent()
    second = update_kb.run(
        home,
        archive=paper_dir,
        service=FakeService([]),
        upload=_fake_upload(),
        run_agent=resumed_agent,
    )

    assert second["reason"] == "done"
    assert second["pending_shards"] == 0
    assert second["copied"] == []
    pending_names = {
        Path(item["archive_path"]).name
        for shard in pending
        for item in shard["files"]
    }
    for name in pending_names:
        assert any(name in call["task"] for call in resumed_agent.calls)


# ------------------------------------------------------------------- upload
def test_pdf_files_are_uploaded_through_the_library_seam(tmp_path):
    fitz = pytest.importorskip("fitz")
    source = tmp_path / "inbox"
    source.mkdir()
    pdf = source / "Paper One.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "hello world")
    doc.save(pdf)
    doc.close()

    upload = _fake_upload()
    home, paper_dir, result = _run(tmp_path, source, upload=upload)

    assert len(upload.calls) == 1
    assert upload.calls[0]["paper_id"] == "pdf_paper_one"
    assert Path(upload.calls[0]["path"]).is_file()
    assert result["upload_failed"] == []
    assert result["unread"] == []


# --------------------------------------------------------- console command
def test_horizon_update_kb_is_implemented_and_dispatches_to_the_runner(tmp_path):
    home = _runtime(tmp_path)
    captured: dict = {}

    def fake_update_kb(runtime, **kwargs):
        captured.update({"runtime": Path(runtime), **kwargs})
        return {"ok": True, "action": "update_kb", "reason": "done", "text": "更新完成"}

    assert console.COMMANDS["/horizon-update-kb"]["implemented"] is True
    result = console.dispatch(
        home,
        "/horizon-update-kb E:\\papers\\inbox",
        update_kb=fake_update_kb,
    )

    assert result["ok"] is True
    assert result["action"] == "update_kb"
    assert captured["source_dir"] == "E:\\papers\\inbox"
    assert captured["runtime"] == home

