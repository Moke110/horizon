"""Lark CLI: private install, login facts, permission preflight, no secrets."""

import json
import os
from pathlib import Path

import pytest

from hostrun import cli, lark, workspace
from conftest import cli_locations

ROOT = Path(__file__).resolve().parents[1]


def _workspace(tmp_path):
    root = tmp_path / "ws"
    home = root / "horizon-archive"
    home.mkdir(parents=True)
    workspace.save_record(
        root,
        {
            "schema_version": 1,
            "root": str(root),
            "repo": str(root / "horizon"),
            "home": str(home),
            "repo_url": workspace.DEFAULT_REPO_URL,
            "commit": "abc123",
        },
    )
    return root, home


def _cli_file(directory, name=None):
    binary = Path(directory) / (
        name or ("lark-cli.exe" if os.name == "nt" else "lark-cli")
    )
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    return binary


def _auth_payload(**over):
    payload = {
        "identities": {
            "user": {"available": True, "tokenStatus": "valid", "userOpenId": "ou_x"},
            "bot": {"available": True, "tokenStatus": "valid"},
            "tenant": {"matched": True},
        },
        "scopes": ["bitable:app", "drive:drive"],
    }
    payload.update(over)
    return payload


class Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_discover_prefers_private_launcher(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    binary = tmp_path / ".horizon" / "bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")

    found = lark.discover_cli(tmp_path)

    assert found["ok"] is True
    assert Path(found["path"]) == binary
    assert found["source"] == "private"


def test_missing_npm_reports_actionable_block(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    result = lark.install_cli(tmp_path)

    assert result["ok"] is False
    assert result["reason"] == "npm_missing"
    assert "Node.js" in result["note"]


def test_private_npm_install_writes_launcher_under_workspace(tmp_path, monkeypatch):
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    npm = bindir / ("npm.cmd" if os.name == "nt" else "npm")
    npm.write_text("", encoding="utf-8")
    node = bindir / ("node.exe" if os.name == "nt" else "node")
    node.write_text("", encoding="utf-8")
    monkeypatch.setenv("PATH", str(bindir))

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if "install" in argv:
            entry = tmp_path / ".horizon" / "lark" / "node_modules" / "@larksuite" / "cli" / "bin"
            entry.mkdir(parents=True, exist_ok=True)
            (entry / "lark-cli.js").write_text("// fake", encoding="utf-8")
            return Proc(stdout="added 1 package")
        return Proc(stdout="lark-cli 1.2.3")

    calls = []
    result = lark.install_cli(tmp_path, runner=runner)

    assert result["ok"] is True
    launcher = Path(result["path"])
    assert launcher.parent == tmp_path / ".horizon" / "bin"
    assert launcher.is_file()
    if os.name == "nt":
        text = launcher.read_text(encoding="utf-8").lower()
        assert "node" in text
        assert "lark-cli.js" in text
    assert any("--prefix" in call for call in calls)
    assert not any("--global" in call or "-g" in call for call in calls)


def test_auth_status_parses_booleans_and_scopes_only():
    facts = lark.parse_auth(_auth_payload())

    assert facts["user"]["ready"] is True
    assert facts["bot"]["ready"] is True
    assert facts["scopes"] == ["bitable:app", "drive:drive"]
    assert "accessToken" not in json.dumps(facts)


def test_expired_token_is_not_ready():
    payload = _auth_payload()
    payload["identities"]["user"]["tokenStatus"] = "expired"
    facts = lark.parse_auth(payload)

    assert facts["user"]["ready"] is False
    assert facts["user"]["expired"] is True


def test_identity_reports_missing_side():
    def runner(argv, **kwargs):
        payload = _auth_payload()
        payload["identities"]["bot"]["available"] = False
        return Proc(stdout=json.dumps(payload))

    result = lark.identity("lark-cli", runner=runner)

    assert result["status"] == "blocked"
    assert "bot" in result["note"]
    assert result["evidence"]["missing"] == ["bot"]


def test_authorization_without_reported_scopes_is_untested():
    result = lark.authorization([], required=["bitable:app"])

    assert result["status"] == "untested"
    assert "不能据此声明" in result["note"]


def test_authorization_reports_missing_scopes():
    result = lark.authorization(["drive:drive"], required=["bitable:app", "drive:drive"])

    assert result["status"] == "blocked"
    assert result["missing"] == ["bitable:app"]


def test_resource_preflight_proves_base_and_drive_reads(tmp_path):
    calls = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if "+table-list" in argv:
            return Proc(stdout=json.dumps({"ok": True, "data": {"tables": []}}))
        if "files" in argv and "list" in argv:
            return Proc(stdout=json.dumps({"ok": True, "data": {"files": []}}))
        return Proc(stdout="{}")

    result = lark.resource_preflight(
        tmp_path,
        "lark-cli",
        base_token="bas_x",
        folders=["fld_src", "fld_push"],
        runner=runner,
    )

    assert result["base"]["status"] == "ok"
    assert result["drive"]["status"] == "ok"
    assert result["messages"]["status"] == "untested"
    # real Base+Drive reads are sufficient; messages stay separate evidence
    assert result["status"] == "ok"
    assert any("--base-token" in call for call in calls)


def test_status_reaches_ok_from_real_base_and_drive_reads(tmp_path):
    home = tmp_path / "home"
    (home / "knowledge").mkdir(parents=True)
    (home / "knowledge" / "base.json").write_text(
        json.dumps({"schema_version": 1, "base_token": "bas_x", "tables": {}}),
        encoding="utf-8",
    )
    (home / "install").mkdir(parents=True)
    (home / "install" / "facilities.json").write_text(
        json.dumps(
            {
                "base": {"token": "bas_x"},
                "folders": {"source": "fld_src", "push": "fld_push"},
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "ws"
    binary = root / ".horizon" / "bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")

    def runner(argv, **kwargs):
        if "auth" in argv:
            return Proc(stdout=json.dumps(_auth_payload()))
        if "+table-list" in argv:
            return Proc(stdout=json.dumps({"ok": True, "data": {"tables": []}}))
        if "files" in argv and "list" in argv:
            return Proc(stdout=json.dumps({"ok": True, "data": {"files": []}}))
        return Proc(stdout="{}")

    result = lark.status(root, home=home, required=[], runner=runner)

    assert result["resources"]["status"] == "ok"
    assert result["resources"]["messages"]["status"] == "untested"
    assert result["ok"] is True


def test_resource_preflight_never_claims_permission_from_a_failure(tmp_path):
    def runner(argv, **kwargs):
        return Proc(stdout=json.dumps({"ok": False, "error": {"code": 99991672}}))

    result = lark.resource_preflight(tmp_path, "lark-cli", base_token="bas_x", runner=runner)

    assert result["base"]["status"] == "blocked"
    assert result["status"] == "blocked"


def test_identity_reports_dpop_key_status_without_secrets():
    payload = _auth_payload(dpopKeyStatus="missing", dpopMode="required")
    payload["identities"]["user"]["accessToken"] = "secret-access"

    result = lark.identity("lark-cli", runner=lambda argv, **kwargs: Proc(stdout=json.dumps(payload)))

    assert result["status"] == "ok"
    assert result["evidence"]["dpop_key_status"] == "missing"
    assert result["evidence"]["dpop_mode"] == "required"
    assert "ou_x" not in json.dumps(result)
    assert "secret-access" not in json.dumps(result)


def test_user_open_id_returns_the_authenticated_user():
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(item) for item in argv])
        return Proc(stdout=json.dumps(_auth_payload()))

    result = lark.user_open_id("lark-cli", runner=runner)

    assert result == {"ok": True, "open_id": "ou_x"}
    assert calls[0][:3] == ["lark-cli", "auth", "status"]
    assert "--verify" in calls[0]


def test_user_open_id_is_absent_when_not_reported():
    payload = _auth_payload()
    del payload["identities"]["user"]["userOpenId"]

    result = lark.user_open_id(
        "lark-cli", runner=lambda argv, **kwargs: Proc(stdout=json.dumps(payload))
    )

    assert result == {"ok": False, "open_id": ""}


def test_user_open_id_rejects_an_unusable_identity():
    """A cached open id is not evidence when the user token is missing."""
    payload = _auth_payload()
    payload["identities"]["user"] = {
        "status": "missing",
        "available": False,
        "tokenStatus": "needs_refresh",
        "userOpenId": "ou_x",
        "refreshExpiresAt": "2099-01-01T00:00:00+08:00",
    }

    result = lark.user_open_id(
        "lark-cli", runner=lambda argv, **kwargs: Proc(stdout=json.dumps(payload))
    )

    assert result == {"ok": False, "open_id": ""}


def test_user_open_id_accepts_a_refreshable_identity():
    payload = _auth_payload()
    payload["identities"]["user"] = {
        "available": True,
        "tokenStatus": "needs_refresh",
        "userOpenId": "ou_x",
        "refreshExpiresAt": "2099-01-01T00:00:00+00:00",
    }

    result = lark.user_open_id(
        "lark-cli", runner=lambda argv, **kwargs: Proc(stdout=json.dumps(payload))
    )

    assert result == {"ok": True, "open_id": "ou_x"}


def test_status_accepts_explicit_tokens_without_writing_the_library(tmp_path):
    home = tmp_path / "home"
    (home / "install").mkdir(parents=True)
    (home / "install" / "facilities.json").write_text(
        json.dumps(
            {
                "base": {"token": "bas_recorded"},
                "folders": {"source": "fld_old", "push": "fld_old_push"},
            }
        ),
        encoding="utf-8",
    )
    (home / "knowledge").mkdir()
    (home / "knowledge" / "base.json").write_text(
        json.dumps({"base_token": "bas_recorded"}), encoding="utf-8"
    )
    root = tmp_path / "ws"
    binary = root / ".horizon" / "bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    before = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    calls = []

    def runner(argv, **kwargs):
        calls.append([str(item) for item in argv])
        if "auth" in argv:
            return Proc(stdout=json.dumps(_auth_payload()))
        if "+table-list" in argv:
            return Proc(stdout=json.dumps({"ok": True, "data": {"tables": []}}))
        if "files" in argv and "list" in argv:
            return Proc(stdout=json.dumps({"ok": True, "data": {"files": []}}))
        return Proc(stdout="{}")

    result = lark.status(
        root,
        home=home,
        required=[],
        runner=runner,
        base_token="bas_probe",
        folders=["fld_probe_one", "fld_probe_two"],
    )

    assert result["resources"]["status"] == "ok"
    joined = [" ".join(call) for call in calls]
    assert any("bas_probe" in call for call in joined)
    assert any("fld_probe_one" in call for call in joined)
    assert any("fld_probe_two" in call for call in joined)
    assert not any("bas_recorded" in call for call in joined)
    after = {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_cli_status_accepts_explicit_tokens(tmp_path, monkeypatch):
    root, home = _workspace(tmp_path)
    captured = {}

    def fake_status(root_arg, **kwargs):
        captured["root"] = Path(root_arg)
        captured.update(kwargs)
        return {
            "ok": True,
            "cli": {"status": "ok", "path": "lark-cli"},
            "identity": {"status": "ok", "note": "", "evidence": {}},
            "authorization": {"status": "ok", "note": "", "granted": [], "missing": []},
            "resources": {"status": "ok", "note": ""},
            "checked_at": "2026-10-29T00:00:00+08:00",
        }

    monkeypatch.setattr(lark, "status", fake_status)

    code = cli.main(
        [
            *cli_locations(home),
            "lark",
            "status",
            "--root",
            str(root),
            "--base-token",
            "bas_probe",
            "--folder",
            "fld_one",
            "--folder",
            "fld_two",
        ]
    )

    assert code == 0
    assert captured["base_token"] == "bas_probe"
    assert captured["folders"] == ["fld_one", "fld_two"]


def test_status_never_echoes_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    binary = tmp_path / ".horizon" / "bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")

    def runner(argv, **kwargs):
        payload = _auth_payload(accessToken="secret-token", refreshToken="refresh-secret")
        return Proc(stdout=json.dumps(payload))

    result = lark.status(tmp_path, required=["bitable:app", "drive:drive"], runner=runner)

    blob = json.dumps(result, ensure_ascii=False)
    assert "secret-token" not in blob
    assert "refresh-secret" not in blob
    assert result["identity"]["status"] == "ok"
    assert result["authorization"]["status"] == "ok"


def test_login_is_guidance_not_a_fake_success(tmp_path):
    guidance = lark.login_command(str(tmp_path / "lark-cli"))

    assert guidance["status"] == "action_required"
    assert "auth login" in guidance["command"]
    assert "凭据" in guidance["storage"]


def test_granular_scopes_satisfy_the_default_requirements():
    granted = [scope for scopes in lark.REQUIRED_SCOPES.values() for scope in scopes]

    result = lark.authorization(granted, required=granted)

    assert result["status"] == "ok"
    assert result["missing"] == []


def test_legacy_broad_grant_covers_granular_requirements():
    result = lark.authorization(
        ["bitable:app", "drive:drive", "im:message:send_as_bot"],
        required=lark.REQUIRED_SCOPES["base"] + lark.REQUIRED_SCOPES["messages"],
    )

    assert result["status"] == "ok"
    assert "更宽的历史授权覆盖" in result["note"]


def test_legacy_requirement_is_covered_by_a_granular_grant():
    result = lark.authorization(["base:record:read", "im:message"], required=["bitable:app"])

    assert result["status"] == "ok"


def test_needs_refresh_with_an_open_window_is_ready():
    payload = _auth_payload()
    payload["identities"]["user"]["tokenStatus"] = "needs_refresh"
    payload["identities"]["user"]["refreshExpiresAt"] = "2999-01-01T00:00:00+08:00"

    facts = lark.parse_auth(payload)

    assert facts["user"]["ready"] is True
    assert facts["user"]["expired"] is False


def test_needs_refresh_past_the_window_is_expired():
    payload = _auth_payload()
    payload["identities"]["user"]["tokenStatus"] = "needs_refresh"
    payload["identities"]["user"]["refreshExpiresAt"] = "2000-01-01T00:00:00+08:00"

    facts = lark.parse_auth(payload)

    assert facts["user"]["ready"] is False
    assert facts["user"]["expired"] is True


# ------------------------------------------- recorded absolute CLI path (ADR 0007)
def test_stale_recorded_cli_is_blocked_even_when_path_has_one(tmp_path, monkeypatch):
    path_dir = tmp_path / "pathbin"
    path_dir.mkdir()
    _cli_file(path_dir, "lark-cli.cmd" if os.name == "nt" else "lark-cli")
    monkeypatch.setenv("PATH", str(path_dir))
    stale = tmp_path / "retired" / ("lark-cli.exe" if os.name == "nt" else "lark-cli")

    found = lark.discover_cli(tmp_path, spec=str(stale), home=tmp_path / "home")

    assert found["ok"] is False
    assert found["stale"] is True
    assert found["path"] == str(stale)
    assert "discover --cli" in found["note"]
    assert "PATH" in found["note"]


def test_status_reports_a_stale_recorded_cli_with_a_rediscovery_step(tmp_path):
    stale = str(tmp_path / "retired" / "lark-cli")

    result = lark.status(tmp_path, spec=stale, home=tmp_path / "home")

    assert result["ok"] is False
    assert result["cli"]["status"] == "blocked"
    assert result["cli"]["stale"] is True
    assert "discover --cli" in result["next"]


def test_discover_cli_persists_the_absolute_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    root, home = _workspace(tmp_path)
    binary = _cli_file(tmp_path / "tools")

    code = cli.main(
        [*cli_locations(home), "lark", "discover", "--root", str(root), "--cli", str(binary)]
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(binary)
    config = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert config["paths"]["lark_cli"] == str(binary)


def test_discover_uses_the_recorded_path_when_cli_is_omitted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    root, home = _workspace(tmp_path)
    binary = _cli_file(tmp_path / "tools")
    from hostrun.base import update_config

    update_config(home, paths={"lark_cli": str(binary)})

    code = cli.main([*cli_locations(home), "lark", "discover", "--root", str(root)])

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(binary)


def test_cli_discover_blocks_a_stale_recorded_path(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "")
    root, home = _workspace(tmp_path)
    stale = tmp_path / "retired" / "lark-cli"
    from hostrun.base import update_config

    update_config(home, paths={"lark_cli": str(stale)})

    code = cli.main([*cli_locations(home), "lark", "discover", "--root", str(root)])

    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["stale"] is True
    assert "discover --cli" in payload["note"]
