"""Regression: every lark-cli subprocess must decode UTF-8 explicitly.

On Chinese Windows the default locale codec is GBK.  lark-cli emits UTF-8 JSON
that contains Chinese Base/table names, so a ``text=True`` subprocess without an
explicit encoding raised ``UnicodeDecodeError`` inside the reader thread during
a real ``/horizon-init`` facility creation (real-acceptance defect,
2026-10-30).  These tests pin the kwargs at the call seams.
"""

from __future__ import annotations

import json

from hostrun import base, lark
from library import admin
from publisher import feishu


class Proc:
    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _assert_utf8(kwargs: dict) -> None:
    assert kwargs.get("encoding") == "utf-8", "lark-cli output must decode as UTF-8"
    assert kwargs.get("errors") == "replace", "invalid bytes must not raise"


def _payload() -> str:
    return json.dumps({"ok": True, "data": {"message_id": "om_x"}}, ensure_ascii=False)


def test_feishu_run_passes_utf8_decoding(monkeypatch):
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return Proc(stdout=_payload())

    monkeypatch.setattr(feishu.subprocess, "run", fake_run)

    payload = feishu._run(["im", "+messages-send"])

    assert payload["ok"] is True
    _assert_utf8(captured)


def test_admin_run_passes_utf8_decoding():
    captured: dict = {}

    def runner(argv, **kwargs):
        captured.update(kwargs)
        return Proc(stdout=_payload())

    admin._run(["lark-cli", "base", "+table-list"], runner=runner)

    _assert_utf8(captured)


def test_hostrun_lark_run_passes_utf8_decoding():
    captured: dict = {}

    def runner(argv, **kwargs):
        captured.update(kwargs)
        return Proc(stdout="{}")

    result = lark._run(["lark-cli", "auth", "status"], runner=runner)

    assert result["ok"] is True
    _assert_utf8(captured)


def test_hostrun_base_run_json_passes_utf8_decoding():
    captured: dict = {}

    def runner(argv, **kwargs):
        captured.update(kwargs)
        return Proc(stdout='{"ok": true}')

    result = base.run_json(["lark-cli", "status"], runner=runner)

    assert result["ok"] is True
    _assert_utf8(captured)
