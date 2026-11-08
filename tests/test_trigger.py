import json
import os
import subprocess

import pytest

from question_queue.store import QueueStore
from trigger.keywords import parse, resolve_checkpoint
from trigger.service import (
    Config,
    handle_event,
    lease_minutes,
    run_pending,
    scheduled_pull,
)


def test_parse_keywords_with_optional_checkpoint():
    assert parse("再推一篇").checkpoint is None
    assert parse("下一篇 hmbs").checkpoint == "hmbs"
    assert parse("NEXT virtual-cell").keyword == "NEXT"
    assert parse("more：HMBS").checkpoint == "HMBS"
    assert parse("今天天气不错") is None


def test_resolve_checkpoint_matches_id_or_label():
    cards = [{"id": "card_hmbs", "label": "HMBS / 羟甲基胆素合酶"}]
    assert resolve_checkpoint("card_hmbs", cards) == "card_hmbs"
    assert resolve_checkpoint("HMBS / 羟甲基胆素合酶", cards) == "card_hmbs"
    assert resolve_checkpoint("nope", cards) == ""
    assert resolve_checkpoint(None, cards) is None


def _config(kl_home):
    return Config(repo=kl_home, home=kl_home, owner="ou_owner", dry_run=True)


def _record_push(kl_home, push_id="2026-10-21_card_hmbs_01", send=True):
    push_dir = kl_home / "archive" / "pushes"
    push_dir.mkdir(parents=True, exist_ok=True)
    (push_dir / f"{push_id}.json").write_text(
        json.dumps({"schema_version": 2, "push_id": push_id, "paper_ids": []}),
        encoding="utf-8",
    )
    if send:
        with (kl_home / "publisher" / "messages.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": "push_file", "push_id": push_id}) + "\n")


def _event(content, sender="ou_owner", chat_type="p2p"):
    return {
        "chat_type": chat_type,
        "sender_type": "user",
        "sender_id": sender,
        "chat_id": "oc_chat",
        "content": content,
        "message_type": "text",
    }


def test_handle_event_ignores_non_owner(kl_home):
    result = handle_event(_event("再推一篇", sender="ou_other"), _config(kl_home))
    assert result == {"ok": False, "reason": "not_owner"}


def test_handle_event_ignores_non_keyword(kl_home):
    result = handle_event(_event("随便聊聊"), _config(kl_home))
    assert result == {"ok": False, "reason": "no_keyword"}


def test_handle_event_runs_one_job_and_replies(kl_home):
    replies = []

    def replier(event, text, config, identity="bot"):
        replies.append(text)
        return {"ok": True}

    def runner(config, prompt):
        assert "HORIZON_RUNTIME=" + str(kl_home) in prompt
        _record_push(kl_home, "2026-10-20_hmbs_01")
        return subprocess.CompletedProcess([], 0, "已推送 2026-10-20_hmbs_01", "")

    result = handle_event(
        _event("再推一篇 card_hmbs"), _config(kl_home), runner=runner, replier=replier
    )
    assert result["ok"] is True
    assert result["checkpoint"] == "card_hmbs"
    assert replies[0] == "收到，开始检索。"
    assert replies[-1] == "补推完成。"
    assert (kl_home / "publisher" / "trigger.log").exists()


def test_handle_event_unknown_checkpoint(kl_home):
    replies = []

    def replier(event, text, config, identity="bot"):
        replies.append(text)
        return {"ok": True}

    result = handle_event(
        _event("下一篇 nope"), _config(kl_home), replier=replier
    )
    assert result == {"ok": False, "reason": "unknown_checkpoint"}
    assert "未知 checkpoint" in replies[0]


def _busy_queue(kl_home):
    queue = QueueStore(kl_home)
    queue.init()
    queue.enqueue_push({"push_id": "push_busy", "proposals": []})
    return queue


def test_manual_request_is_queued_when_queue_busy(kl_home):
    _busy_queue(kl_home)
    called = []
    replies = []

    def runner(config, prompt):
        called.append(prompt)
        return subprocess.CompletedProcess([], 0, "已推送 x", "")

    def replier(event, text, config, identity="bot"):
        replies.append(text)
        return {"ok": True}

    result = handle_event(
        _event("再推一篇 card_hmbs"), _config(kl_home), runner=runner, replier=replier
    )
    assert result["queued"] is True
    assert result["reason"] == "queue_busy"
    assert called == []
    assert "已记下" in replies[-1]
    request = QueueStore(kl_home).status()["request"]
    assert request["sources"] == ["manual"]
    assert request["card_id"] == "card_hmbs"


def test_scheduled_records_only_when_queue_busy(kl_home):
    _busy_queue(kl_home)
    called = []

    def runner(config, prompt):
        called.append(prompt)
        return subprocess.CompletedProcess([], 0, "已推送 x", "")

    result = scheduled_pull(_config(kl_home), runner=runner)
    assert result["queued"] is True
    assert result["reason"] == "queue_busy"
    assert called == []
    request = QueueStore(kl_home).status()["request"]
    assert request["sources"] == ["scheduled"]


def test_scheduled_starts_and_clears_request_when_queue_empty(kl_home):
    called = []

    def runner(config, prompt):
        called.append(prompt)
        _record_push(kl_home, "2026-10-21_card_hmbs_01")
        return subprocess.CompletedProcess([], 0, "已推送 2026-10-21_card_hmbs_01", "")

    result = scheduled_pull(_config(kl_home), runner=runner)
    assert result["ok"] is True
    assert len(called) == 1
    assert QueueStore(kl_home).status()["request"] is None


def test_manual_and_scheduled_coalesce_into_one_start(kl_home):
    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("manual", "card_hmbs")
    queue.request_push("scheduled")
    first = queue.try_start()
    second = queue.try_start()
    assert first["start"] is True
    assert second["reason"] == "starting"
    request = queue.status()["request"]
    assert request["sources"] == ["manual", "scheduled"]
    assert request["count"] == 2


def test_run_pending_executes_the_merged_request(kl_home):
    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("manual", "card_hmbs")
    called = []

    def runner(config, prompt):
        called.append(prompt)
        assert "本次只处理知识卡片：card_hmbs" in prompt
        _record_push(kl_home, "2026-10-21_card_hmbs_01")
        return subprocess.CompletedProcess([], 0, "已推送 x", "")

    result = run_pending(_config(kl_home), runner=runner)
    assert result["ok"] is True
    assert len(called) == 1
    assert queue.status()["request"] is None


def test_lease_covers_the_agent_timeout(kl_home):
    config = _config(kl_home)
    config.timeout = 1800
    minutes = lease_minutes(config)
    assert minutes > config.timeout / 60
    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("pending")
    start = queue.try_start(minutes)
    assert start["lease_minutes"] == minutes
    # The retry timer must see an active lease and stay out of the way.
    assert queue.try_start(minutes)["reason"] == "starting"


def test_job_lock_contention_leaves_request_alone(kl_home):
    from trigger.service import job_lock

    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("pending")
    with job_lock(kl_home) as acquired:
        assert acquired
        result = run_pending(_config(kl_home))
    assert result["reason"] == "busy"
    request = QueueStore(kl_home).status()["request"]
    assert request is not None
    assert request["attempts"] == 0


def test_run_without_a_sent_push_is_a_failure(kl_home):
    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("pending")

    def runner(config, prompt):
        _record_push(kl_home, "2026-10-22_card_hmbs_01", send=False)
        return subprocess.CompletedProcess([], 0, "已推送 2026-10-22_card_hmbs_01", "")

    result = run_pending(_config(kl_home), runner=runner)
    assert result["ok"] is False
    assert result["reason"].startswith("push_not_sent:")
    rows = [
        json.loads(line)
        for line in (kl_home / "publisher" / "trigger.log").read_text().splitlines()
    ]
    assert rows[-1]["kind"] == "finish"
    assert rows[-1]["reason"].startswith("push_not_sent:")


def test_run_without_any_push_is_a_failure(kl_home):
    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("pending")

    def runner(config, prompt):
        return subprocess.CompletedProcess([], 0, "我会再试一次", "")

    result = run_pending(_config(kl_home), runner=runner)
    assert result["ok"] is False
    assert result["reason"] == "no_push_output"


def test_timeout_logs_the_agent_tail(kl_home):
    queue = QueueStore(kl_home)
    queue.init()
    queue.request_push("pending")

    def runner(config, prompt):
        raise subprocess.TimeoutExpired(
            ["opencode"], 1800, output="half way", stderr="still writing"
        )

    result = run_pending(_config(kl_home), runner=runner)
    assert result["reason"] == "timeout"
    rows = [
        json.loads(line)
        for line in (kl_home / "publisher" / "trigger.log").read_text().splitlines()
    ]
    timeout_row = next(row for row in rows if row["kind"] == "timeout")
    assert "half way" in timeout_row["stdout_tail"]
    assert "still writing" in timeout_row["stdout_tail"]
    # A failed start keeps the pending request for the retry timer.
    assert QueueStore(kl_home).status()["request"] is not None


@pytest.mark.skipif(
    os.name == "nt",
    reason="process groups (getpgid/killpg) are POSIX-only; Windows uses taskkill",
)
def test_run_agent_kills_process_group_on_timeout(monkeypatch, tmp_path):
    import signal

    from trigger import service as trigger_service

    class FakeProc:
        pid = 43210
        args = ["opencode", "run"]
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if timeout is not None:
                raise subprocess.TimeoutExpired(self.args, timeout)
            return ("", "")

    killed = []
    proc = FakeProc()
    monkeypatch.setattr(trigger_service.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(trigger_service.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(trigger_service.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    config = Config(repo=tmp_path, home=tmp_path, owner="ou_owner", timeout=1)
    with pytest.raises(subprocess.TimeoutExpired):
        trigger_service.run_agent(config, "prompt")
    assert killed == [(43210, signal.SIGKILL)]
    assert proc.calls == 2
