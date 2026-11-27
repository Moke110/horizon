"""Host-run config base: recorded ``dsh`` path, Agent-run defaults, redaction.

The Console (ADR 0009) is the only resident component; everything its Agent runs
need is recorded configuration, so these tests stay offline: no real ``dsh``, no
PATH fallback, no network or credentials.
"""

import json
import os
from pathlib import Path

import pytest

from hostrun import base


def _write_config(home: Path, config: dict) -> Path:
    path = Path(home) / "install" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path






# ---------------------------------------------------------------- defaults
def test_new_config_records_only_current_agent_run_defaults(tmp_path):
    config = base.default_config(tmp_path / "home")

    assert "dsh" not in config["paths"]
    assert config["agent_run"] == {
        "feedback_lease_minutes": 45,
        "derived_timeout_seconds": 1200,
    }
    assert "listener" not in config
    assert "schedules" not in config


def test_load_config_fills_agent_run_defaults_without_overwriting(tmp_path):
    home = tmp_path / "home"
    _write_config(
        home,
        {
            "schema_version": 1,
            "paths": {"dsh": "D:/tools/dsh.cmd"},
            "agent_run": {"feedback_lease_minutes": 10},
        },
    )

    config = base.load_config(home)

    # recorded values win ...
    assert config["paths"]["dsh"] == "D:/tools/dsh.cmd"
    assert config["agent_run"]["feedback_lease_minutes"] == 10
    # ... and the rest of the section is filled from the defaults.
    assert config["agent_run"]["derived_timeout_seconds"] == 1200
    assert "dsh_args" not in config["agent_run"]
    # unrelated sections keep their defaults too.
    assert config["paths"]["python"]


def test_legacy_listener_config_is_mapped_onto_agent_run(tmp_path):
    """A migrated install keeps internal run settings without the retired section."""
    home = tmp_path / "home"
    _write_config(
        home,
        {
            "schema_version": 1,
            "listener": {
                "start_command": "C:/old/horizon-listener.cmd",
                "stop_command": "C:/old/horizon-listener-stop.cmd",
                "dsh_args": ["--patch", "C:/overlay.yml"],
                "feedback_lease_minutes": 30,
            },
            "schedules": {"enabled": True, "tasks": [{"id": "horizon-daily"}]},
        },
    )

    config = base.load_config(home)

    assert "dsh_args" not in config["agent_run"]
    assert config["agent_run"]["feedback_lease_minutes"] == 30
    assert config["agent_run"]["derived_timeout_seconds"] == 1200
    assert "listener" not in config
    assert "schedules" not in config


def test_agent_run_and_dsh_round_trip(tmp_path):
    home = tmp_path / "home"

    base.update_config(
        home,
        paths={"dsh": "C:/tools/dsh.cmd"},
        agent_run={"dsh_args": ["--patch", "C:/overlay.yml"]},
    )
    loaded = base.load_config(home)

    assert loaded["paths"]["dsh"] == "C:/tools/dsh.cmd"
    assert loaded["agent_run"]["dsh_args"] == ["--patch", "C:/overlay.yml"]


def test_redaction_masks_credentials_but_keeps_paths_and_agent_run(tmp_path):
    home = tmp_path / "home"
    config = base.default_config(home)
    config["paths"]["dsh"] = "C:/tools/dsh.cmd"
    config["paths"]["lark_cli"] = "C:/tools/lark-cli.cmd"
    config["app_secret"] = "s3cret"
    config["agent_run"]["dsh_args"] = ["--patch", "C:/overlay.yml"]

    base.save_config(home, config)

    written = json.loads((home / "install" / "config.json").read_text(encoding="utf-8"))
    assert written["app_secret"] == "***"
    assert written["paths"]["dsh"] == "C:/tools/dsh.cmd"
    assert written["paths"]["lark_cli"] == "C:/tools/lark-cli.cmd"
    assert written["agent_run"]["dsh_args"] == ["--patch", "C:/overlay.yml"]
