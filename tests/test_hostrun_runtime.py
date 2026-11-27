"""Lease, bounded intents, existing-data audit and the CLI surface."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import seed_queue, cli_locations
from hostrun import audit, cli, executor, feedback, install, intents


def _at(minutes: int = 0) -> datetime:
    return datetime(2026, 10, 21, 8, 0, tzinfo=timezone(timedelta(hours=8))) + timedelta(minutes=minutes)


# ----------------------------------------------------------------- executor
def test_lease_blocks_a_second_host_and_records_who_holds_it(tmp_path):
    first = executor.acquire(tmp_path, host="workbuddy", at=_at())
    assert first["ok"] is True
    assert executor.status(tmp_path, at=_at(1))["state"] == "held"

    blocked = executor.acquire(tmp_path, host="chatgpt", at=_at(1))
    assert blocked["ok"] is False
    assert blocked["reason"] == "executor_busy"
    assert blocked["holder"] == "workbuddy"

    events = (tmp_path / "executor" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["kind"] == "blocked" for line in events)


def test_expired_lease_is_taken_over_and_logged(tmp_path):
    executor.acquire(tmp_path, host="workbuddy", at=_at(), minutes=10)
    assert executor.status(tmp_path, at=_at(30))["state"] == "stale"

    second = executor.acquire(tmp_path, host="chatgpt", at=_at(30))

    assert second["ok"] is True
    assert second["took_over"] is True
    events = [json.loads(line) for line in
              (tmp_path / "executor" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(row["kind"] == "taken_over" and row["previous"] == "workbuddy" for row in events)


def test_heartbeat_and_release_belong_to_the_holder_only(tmp_path):
    executor.acquire(tmp_path, host="workbuddy", at=_at(), minutes=5)

    assert executor.heartbeat(tmp_path, host="chatgpt", at=_at(1))["ok"] is False
    assert executor.heartbeat(tmp_path, host="workbuddy", at=_at(1))["ok"] is True
    assert executor.status(tmp_path, at=_at(40))["state"] == "held"  # extended
    assert executor.release(tmp_path, host="chatgpt")["reason"] == "not_holder"
    assert executor.release(tmp_path, host="workbuddy")["released"] is True
    assert executor.status(tmp_path, at=_at(40))["state"] == "free"


def test_handover_needs_the_old_executor_stopped(tmp_path):
    executor.acquire(tmp_path, host="workbuddy", minutes=60)  # live from now

    refused = executor.handover(tmp_path, to_host="chatgpt", from_host="workbuddy")
    assert refused["ok"] is False
    assert refused["reason"] == "old_executor_active"
    assert executor.status(tmp_path)["state"] == "held"

    wrong_from = executor.handover(tmp_path, to_host="chatgpt", from_host="chatgpt", force=True)
    assert wrong_from["ok"] is False
    assert wrong_from["reason"] == "unexpected_holder"

    forced = executor.handover(tmp_path, to_host="chatgpt", from_host="workbuddy", force=True)
    assert forced["ok"] is True
    assert forced["forced"] is True
    assert executor.status(tmp_path)["state"] == "free"


# ------------------------------------------------------------------ intents
class FakeLibrary:
    def __init__(self, reconcile=None, usage=None, alert=None):
        self._reconcile = reconcile or {"ok": True, "counts": {}, "report_path": "r.json"}
        self._usage = usage or {"used": 10, "limit": 5120, "percent": 0.2, "level": "ok"}
        self._alert = alert or {}

    def reconcile(self, dry_run=False):
        return self._reconcile

    def usage(self):
        return self._usage

    def maybe_alert(self, reason=""):
        return self._alert


def _item(item_id="q_01", status="active"):
    return {
        "id": item_id,
        "kind": "rating",
        "push_id": "2026-10-21_card_hmbs_01",
        "card_id": "card_hmbs",
        "status": status,
        "created_at": "2026-10-21T08:00:00+08:00",
        "sent_at": "2026-10-21T08:01:00+08:00",
        "answered_at": None,
        "message_id": "om_1",
        "attempts": 0,
        "answer": None,
        "payload": {"target_proposals": [], "proposals": [], "ready": True},
    }


def _home(configured_home, offline_base, items=None):
    feedback.setup(configured_home, client=offline_base)
    seed_queue(configured_home, items or [])
    feedback.sync(configured_home, client=offline_base)
    return configured_home


def test_feedback_intent_never_creates_a_retrieval_request(configured_home, offline_base):
    home = _home(configured_home, offline_base, [_item()])

    result = intents.run(home, "feedback", host="workbuddy", client=offline_base, offline=True)

    assert result["ok"] is True
    assert result["retrieval"]["requested"] is False
    assert result["retrieval"]["started"] is False
    assert "request:daily" not in result["actions"]
    assert not (home / "queue" / "request.json").exists()


def test_daily_intent_requests_one_push_and_folds_maintenance_in(configured_home, offline_base):
    home = _home(configured_home, offline_base, [])
    install.record(home, "knowledge", "ok")
    library = FakeLibrary()

    result = intents.run(
        home, "daily", host="workbuddy", client=offline_base, library=library, date="2026-10-21"
    )

    assert "request:daily" in result["actions"]
    assert result["retrieval"]["started"] is True
    assert result["brief"] and "一篇论文推送" in result["brief"]
    assert result["maintenance"]["ok"] is True
    assert (home / "library" / "maintenance" / "2026-10-21.json").exists()


def test_maintenance_runs_at_most_once_per_day(configured_home, offline_base):
    home = _home(configured_home, offline_base, [])
    library = FakeLibrary()

    first = intents.run(home, "maintenance", host="workbuddy", client=offline_base,
                        library=library, date="2026-10-21")
    second = intents.run(home, "maintenance", host="workbuddy", client=offline_base,
                         library=library, date="2026-10-21")

    assert first["maintenance"]["ok"] is True
    assert "maintenance" not in second
    assert intents.maintenance_due(home, date="2026-10-22") is True


def test_a_failed_maintenance_is_retried_not_marked_done(configured_home, offline_base):
    home = _home(configured_home, offline_base, [])
    broken = FakeLibrary(reconcile={"ok": False, "counts": {}, "report_path": ""})

    result = intents.run(home, "maintenance", host="workbuddy", client=offline_base,
                         library=broken, date="2026-10-21")

    assert result["maintenance"]["ok"] is False
    assert result["quiet"] is False
    assert result["notify"]
    assert intents.maintenance_due(home, date="2026-10-21") is True


def test_second_run_on_another_host_says_the_executor_is_busy(configured_home, offline_base):
    home = _home(configured_home, offline_base, [])
    executor.acquire(home, host="workbuddy", minutes=60)

    result = intents.run(home, "feedback", host="chatgpt", client=offline_base, offline=True)

    assert result["ok"] is False
    assert result["quiet"] is True
    assert result["reason"] == "executor_busy"


def test_run_owns_its_lease_by_default(configured_home, offline_base):
    """#72 item 3: a standalone bounded run still acquires and releases."""
    home = _home(configured_home, offline_base, [])

    result = intents.run(home, "feedback", host="workbuddy", client=offline_base, offline=True)

    assert result["ok"] is True
    assert executor.status(home)["state"] == "free"


def test_run_with_a_caller_held_lease_does_not_acquire_or_release(
    configured_home, offline_base, monkeypatch
):
    """#72 item 3: the caller-held lease survives the inner bounded run."""
    home = _home(configured_home, offline_base, [])
    executor.acquire(home, host="workbuddy", minutes=45)
    calls = []
    monkeypatch.setattr(intents.executor, "acquire", lambda *a, **k: calls.append("acquire"))
    monkeypatch.setattr(intents.executor, "release", lambda *a, **k: calls.append("release"))

    result = intents.run(
        home,
        "feedback",
        host="workbuddy",
        client=offline_base,
        offline=True,
        lease=False,
    )

    assert result["ok"] is True
    assert calls == []
    assert executor.status(home)["state"] == "held"


# -------------------------------------------------------------------- audit
class FakeDrive:
    """One Drive with per-folder listings, so source and push sizes stay distinct."""

    def __init__(self, sizes):
        self.by_folder = {"source-folder": list(sizes), "push-folder": []}

    def list(self, folder):
        return [{"name": f"f{i}.pdf", "type": "file", "size": size}
                for i, size in enumerate(self.by_folder.get(folder, []))]


class AuditLibrary:
    tables = {"sources": "tblsrc", "pushes": "tblpush", "papers": "tblpapers"}

    def __init__(self, sources, pushes=None, papers=None, sizes=None):
        self._sources = sources
        self._pushes = pushes or []
        self._papers = papers or []
        self.drive = FakeDrive(sizes or [50, 50])

    def rows(self, key):
        return {self.tables["sources"]: self._sources,
                self.tables["pushes"]: self._pushes,
                "papers": self._papers}.get(key if key in ("papers",) else self.tables.get(key), [])

    def usage(self):
        return {"used": 100}

    def folder(self, kind):
        return f"{kind}-folder"


def _source(paper_id, status="在库", parts=""):
    return {"record_id": f"rec_{paper_id}", "fields": {"原文 ID": paper_id, "状态": status, "分片": parts}}


def test_audit_reports_missing_baseline_entries(tmp_path):
    home = tmp_path / "home"
    baseline = tmp_path / "baseline.jsonl"
    baseline.write_text("\n".join(json.dumps({"paper_id": p}) for p in ("p1", "p2")), encoding="utf-8")
    library = AuditLibrary([_source("p1")])

    report = audit.audit(home, library=library, baseline=baseline)

    assert report["ok"] is False
    assert report["counts"]["missing"] == 1
    assert report["missing"] == ["p2"]
    assert any("基线原文" in gap for gap in report["gaps"])


def test_audit_passes_only_when_everything_lines_up(tmp_path):
    home = tmp_path / "home"
    baseline = tmp_path / "baseline.jsonl"
    baseline.write_text(json.dumps({"paper_id": "p1"}), encoding="utf-8")
    library = AuditLibrary([_source("p1")], sizes=[60, 40])

    report = audit.audit(home, library=library, baseline=baseline)

    assert report["ok"] is True
    assert report["zero_diff"] is True
    assert report["quota"]["within_1_percent"] is True


def test_audit_flags_part_order_and_dangling_refs(tmp_path):
    home = tmp_path / "home"
    library = AuditLibrary(
        [_source("p1", parts=f"p1.pdf.part02,p1.pdf.part01")],
        pushes=[],
        papers=[{"record_id": "rec1", "fields": {"paper_id": "p1", "push_id": "gone"}}],
    )

    report = audit.audit(home, library=library)

    assert report["counts"]["part_problems"] == 1
    assert report["counts"]["dangling_push_refs"] == 1
    assert any("不存在的推送" in gap for gap in report["gaps"])
    assert report["blocked"]  # no baseline given: the 174 backfill is unverified


def test_audit_records_a_zero_diff_streak(tmp_path):
    home = tmp_path / "home"
    for day in ("2026-10-19", "2026-10-20", "2026-10-21"):
        audit.record(home, {"zero_diff": True}, day=day)
    audit.record(home, {"zero_diff": False}, day="2026-10-18")

    result = audit.streak(home, end="2026-10-21")

    assert result["days"] == 3
    assert result["satisfied"] is True
    assert result["dates"] == ["2026-10-19", "2026-10-20", "2026-10-21"]


def test_audit_missing_baseline_file_is_an_error(tmp_path):
    with pytest.raises(Exception):
        audit.audit(tmp_path, library=AuditLibrary([]), baseline=tmp_path / "nope.jsonl")


# ---------------------------------------------------------------------- cli
def test_cli_reports_install_status_as_json(tmp_path, capsys):
    code = cli.main([*cli_locations(tmp_path), "install", "status"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["steps"]["workspace"] == "pending"
    assert {"host", "skill"}.isdisjoint(payload["steps"])


def test_cli_feedback_setup_and_status_offline(configured_home, offline_base, capsys):
    assert cli.main([*cli_locations(configured_home), "--offline", "feedback", "setup"]) == 0
    capsys.readouterr()
    assert cli.main([*cli_locations(configured_home), "--offline", "feedback", "status"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["questions"] == 0
    assert payload["quiet"] is True


def test_cli_rejects_an_unknown_intent(configured_home, capsys):
    with pytest.raises(SystemExit):
        cli.main([*cli_locations(configured_home), "run", "--intent", "hourly"])


# ------------------------------------------------------------- retired paths
def test_host_schedule_commands_are_gone(configured_home, capsys):
    """#91 retires the two host tasks; the Console owns scheduling now."""
    with pytest.raises(SystemExit):
        cli.main([*cli_locations(configured_home), "schedules", "plan"])

    with pytest.raises(SystemExit):
        cli.main([*cli_locations(configured_home), "run", "--intent", "listener-check"])


def test_location_flags_work_after_the_subcommand(configured_home, capsys):
    code = cli.main(["install", "status", *cli_locations(configured_home)])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["steps"]
