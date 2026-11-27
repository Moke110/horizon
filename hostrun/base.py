"""Config, Base-client factory and executable resolution for host runs.

Two rules from ADR 0005 shape this module:

- every external executable comes from explicit configuration first (private
  directory, then a read-only PATH lookup as a last resort) — never from a
  persistent PATH/environment/registry write;
- nothing here prints or stores credentials. Identity checks report booleans
  and status words only.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from common import read_json, write_json

CONFIG_REL = Path("install") / "config.json"
STATE_REL = Path("install") / "state.json"
BASE_REL = Path("knowledge") / "base.json"

#: Config keys the retired listener section used to own but the Console's Agent
#: runs still consume.  ``load_config`` maps an old ``listener`` record onto the
#: current ``agent_run`` section so a migrated installation keeps its overlay.
AGENT_RUN_KEYS = ("feedback_lease_minutes", "derived_timeout_seconds")

DEFAULT_HOST = "unknown"
REDACT_KEYS = ("token", "secret", "password", "cookie", "code", "key")


class HostRunError(RuntimeError):
    pass


# ------------------------------------------------------------------- config
def default_config(home: Path, *, repo: Path | None = None) -> dict:
    home = Path(home)
    return {
        "schema_version": 1,
        "host": {"product": DEFAULT_HOST, "version": "", "mode": ""},
        "paths": {
            "repo": str(Path(repo) if repo else Path(__file__).resolve().parents[1]),
            "home": str(home),
            "python": sys.executable,
            "lark_cli": "",
        },
        "timezone": "",
        "permissions": {"scope": [], "granted_at": None},
        # Agent runs are started by the Console (ADR 0009 §10).  These are the
        # only run settings; the retired listener section is mapped onto them by
        # ``load_config`` so an old config file keeps working.
        "agent_run": {
            "feedback_lease_minutes": 45,
            "derived_timeout_seconds": 1200,
        },
    }


def _mapped_agent_run(cfg: dict) -> dict:
    """The current ``agent_run`` section, plus any legacy ``listener`` values."""
    recorded = dict(cfg.get("agent_run") or {})
    legacy = cfg.get("listener") or {}
    for key in AGENT_RUN_KEYS:
        if key in legacy and key not in recorded:
            recorded[key] = legacy[key]
    return recorded


def load_config(home: Path, *, repo: Path | None = None) -> dict:
    home = Path(home)
    cfg = read_json(home / CONFIG_REL) or {}
    base = default_config(home, repo=repo)
    merged = {**base, **cfg}
    merged["paths"] = {**base["paths"], **(cfg.get("paths") or {})}
    merged["host"] = {**base["host"], **(cfg.get("host") or {})}
    merged["permissions"] = {**base["permissions"], **(cfg.get("permissions") or {})}
    merged["agent_run"] = {**base["agent_run"], **_mapped_agent_run(cfg)}
    # The retired section is never handed out under its old name: everything it
    # still owned now lives under ``agent_run``.
    merged.pop("listener", None)
    merged.pop("schedules", None)
    return merged


def save_config(home: Path, config: dict) -> Path:
    path = Path(home) / CONFIG_REL
    write_json(path, redact(config))
    return path


def update_config(home: Path, **sections) -> dict:
    home = Path(home)
    cfg = load_config(home)
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key] = {**cfg[key], **value}
        else:
            cfg[key] = value
    save_config(home, cfg)
    return cfg


def redact(obj):
    """Strip anything that looks like a credential before it is written out."""
    if isinstance(obj, dict):
        return {
            key: ("***" if any(mark in str(key).lower() for mark in REDACT_KEYS) else redact(value))
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact(item) for item in obj]
    return obj


# ---------------------------------------------------------------- base client
def base_config(home: Path) -> dict:
    cfg = read_json(Path(home) / BASE_REL) or {}
    if not cfg.get("base_token"):
        raise HostRunError(
            f"missing Bitable config: {Path(home) / BASE_REL}（先运行 hostrun kb attach/create 或 library.cli setup）"
        )
    return cfg


def save_base_config(home: Path, config: dict) -> Path:
    path = Path(home) / BASE_REL
    write_json(path, config)
    return path


def lark_bin(home: Path) -> str:
    """The recorded private CLI path, never a bare PATH lookup when recorded."""
    home = Path(home)
    cfg = read_json(home / CONFIG_REL) or {}
    return str((cfg.get("paths") or {}).get("lark_cli") or "lark-cli")


def base_client(
    home: Path,
    *,
    identity: str = "user",
    offline: bool = False,
    client=None,
    bin: str | None = None,
):
    """Return a Base client exposing list/create/update/field_names/create_table.

    ``offline`` swaps the lark-cli client for the persisted JSON fixture, which
    is what demos and tests use so no live tenant is ever touched by accident.
    The live client uses the recorded private CLI path (``bin=`` overrides it
    for an explicit probe).
    """
    if client is not None:
        return client
    home = Path(home)
    if offline:
        from library.offline import JsonBase

        return JsonBase(home / "library" / "offline-base.json")
    from library.base import LarkBaseClient

    return LarkBaseClient(base_config(home), identity=identity, bin=bin or lark_bin(home))


def table_id(config: dict, key: str) -> str:
    table = (config.get("tables") or {}).get(key)
    if not table:
        raise HostRunError(f"Bitable config has no table for {key!r}")
    return table


# -------------------------------------------------------------- executables
def resolve_executable(
    spec: str | None,
    *,
    private_dirs: list[Path] | None = None,
    env: dict | None = None,
    names: list[str] | None = None,
) -> dict:
    """Resolve an executable without touching PATH, environment or registry.

    Order: explicit path → private directories → read-only PATH lookup. The
    returned ``source`` says which one won, so an install report can tell an
    explicit private install apart from an accident of the caller's PATH.
    """
    env = env if env is not None else os.environ
    candidates: list[str] = []
    if spec:
        candidates.append(str(spec))
    for name in names or []:
        candidates.append(name)

    for index, candidate in enumerate(candidates):
        explicit = bool(spec) and index == 0
        path = Path(candidate).expanduser()
        if path.is_absolute() or path.parent != Path("."):
            if path.is_file():
                return {"found": True, "path": str(path), "source": "explicit"}
            continue
        for directory in private_dirs or []:
            local = Path(directory) / candidate
            if local.is_file():
                return {"found": True, "path": str(local), "source": "private"}
        found = shutil.which(candidate, path=env.get("PATH", ""))
        if found:
            return {
                "found": True,
                "path": found,
                "source": "explicit_path_lookup" if explicit else "path",
            }
    return {"found": False, "path": "", "source": "missing"}


def run_json(argv: list[str], *, runner=subprocess.run, timeout: int = 30) -> dict:
    """Run one command that answers with JSON; never echo raw stdout on error."""
    if not argv:
        return {"ok": False, "error": "empty argv"}
    try:
        proc = runner(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "").strip()
    if not out:
        return {"ok": False, "error": f"no output (exit {proc.returncode})"}
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"non-JSON output (exit {proc.returncode})"}
    return {"ok": True, "payload": payload, "exit": proc.returncode}
