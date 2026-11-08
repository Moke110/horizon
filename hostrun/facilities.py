"""Create or attach the Feishu facilities horizon needs, by confirmed names.

One Base holds every table; two Drive folders hold originals and push
artifacts.  The agent creates the Base itself — the user is not asked to
prepare an empty Base — and records stable IDs, not display names:

    <home>/install/facilities.json
    {
      "names":    {"base": ..., "tables": {key: display name}, "folders": {...}},
      "base":     {"token": ..., "url": ..., "name": ...},
      "tables":   {key: {"id": ..., "name": ...}},
      "folders":  {"root": token, "source": token, "push": token}
    }

Rules:

- display names are confirmed by the user before anything is created; reruns
  reuse the recorded IDs and never create a second Base with the same name;
- ``create`` always makes a *new* Base and never adopts a Base found by display
  name, because the default name may match a library the user already owns;
  same-name Bases are reported as a warning so the user can rename first, and
  ``attach`` is the explicit way to connect an existing Base;
- attaching an existing Base inspects identity, structure and content, reports
  missing pieces and never clears or rebuilds them;
- a partial creation is reported with the actions that did happen; rerunning
  finds the created pieces by name and continues.
"""

from __future__ import annotations

from pathlib import Path

from common import now, read_json, write_json
from hostrun import feedback as feedback_mod
from hostrun import install as install_mod
from hostrun import kb
from hostrun.base import HostRunError, base_client
from library.admin import AdminError, LarkAdminClient
from library.drive import LarkDrive

FACILITIES_REL = Path("install") / "facilities.json"
NAMES_REL = Path("install") / "facilities-names.json"
DEFAULT_BASE = "horizon 知识库"
DEFAULT_TABLES = {**kb.KNOWLEDGE_TABLES, **kb.LIBRARY_TABLES, **feedback_mod.TABLE_NAMES}
DEFAULT_FOLDERS = {"root": "horizon", "source": "原文", "push": "推送"}
KEYS = tuple(DEFAULT_TABLES)


def default_names() -> dict:
    return {
        "base": DEFAULT_BASE,
        "tables": dict(DEFAULT_TABLES),
        "folders": dict(DEFAULT_FOLDERS),
    }


def _check_name(value: str, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise HostRunError(f"{label} 名称不能为空")
    if any(char in text for char in ("/", "\\", "?", "*", ":", '"', "<", ">", "|")):
        raise HostRunError(f"{label} 名称不能包含路径或表名非法字符：{text}")
    return text


def normalize(overrides: dict | None = None) -> dict:
    """Merge user choices over defaults, rejecting empty/duplicate names."""
    names = default_names()
    overrides = overrides or {}
    if overrides.get("base"):
        names["base"] = _check_name(overrides["base"], "Base")
    for key, value in (overrides.get("tables") or {}).items():
        if key not in DEFAULT_TABLES:
            raise HostRunError(f"未知的表键：{key}")
        names["tables"][key] = _check_name(value, f"表 {key}")
    for key, value in (overrides.get("folders") or {}).items():
        if key not in DEFAULT_FOLDERS:
            raise HostRunError(f"未知的目录键：{key}")
        names["folders"][key] = _check_name(value, f"目录 {key}")
    tables = list(names["tables"].values())
    if len(set(tables)) != len(tables):
        raise HostRunError("表展示名称不能重复")
    folders = list(names["folders"].values())
    if len(set(folders)) != len(folders):
        raise HostRunError("目录展示名称不能重复")
    return names


def _same_name_warning(admin, names: dict) -> dict | None:
    """Report same-name Bases as read-only information; never adopt them.

    ``create`` always makes a new Base, so this is only a pre-flight hint: the
    user may prefer to rename, or to connect the existing library explicitly
    with ``attach``. A failed lookup must not turn into a creation blocker.
    """
    if admin is None:
        return None
    try:
        resolved = admin.resolve_title(names["base"])
    except Exception as exc:  # noqa: BLE001 - a warning cannot block creation
        return {
            "count": None,
            "names": [],
            "error": str(exc),
            "note": f"无法确认是否已有同名 Base：{exc}",
        }
    matches = (resolved or {}).get("matches") or []
    if not matches:
        return None
    labels = [str(match.get("name") or names["base"]) for match in matches]
    return {
        "count": len(matches),
        "names": labels,
        "note": (
            f"已存在 {len(matches)} 个同名 Base（{'、'.join(labels)}）；"
            "create 会新建一个独立 Base，不会复用或修改它们；"
            "如需连接已有库请用 facilities attach，或先换一个名称"
        ),
    }


def plan(home: Path, overrides: dict | None = None, *, admin=None) -> dict:
    names = normalize(overrides)
    payload = {
        "ok": True,
        "data_root": str(Path(home).expanduser().resolve()),
        "names": names,
        "defaults": default_names(),
        "confirmed": False,
        "needs_user_confirmation": True,
        "note": "确认 Base、各表与两个目录的名称后再创建；确认前不会写入飞书",
    }
    warning = _same_name_warning(admin, names)
    if warning:
        payload["existing_same_name"] = warning
    return payload


def confirm_names(home: Path, names: dict) -> dict:
    payload = {"schema_version": 1, "confirmed_at": now(), "names": names}
    path = Path(home) / NAMES_REL
    write_json(path, payload)
    return {"ok": True, "path": str(path), "confirmed_at": payload["confirmed_at"]}


def load_confirmed(home: Path) -> dict | None:
    payload = read_json(Path(home) / NAMES_REL) or {}
    return payload.get("names")


def load_facilities(home: Path) -> dict:
    return read_json(Path(home) / FACILITIES_REL) or {}


def save_facilities(home: Path, payload: dict) -> Path:
    path = Path(home) / FACILITIES_REL
    write_json(path, {**payload, "updated_at": now()})
    return path


def _client_for(home: Path, client, *, cli_bin: str):
    if client is not None:
        return client
    from library.base import LarkBaseClient

    config = read_json(Path(home) / "knowledge" / "base.json") or {}
    if not config.get("base_token"):
        raise HostRunError("knowledge/base.json 尚无 base_token")
    return LarkBaseClient(config, bin=cli_bin)


def _tables_snapshot(client, configured: dict, names: dict) -> tuple[dict, list[str]]:
    listing = client.list_tables()
    by_id = {row.get("id") or row.get("table_id"): row.get("name") for row in listing}
    by_name = {row.get("name"): row.get("id") or row.get("table_id") for row in listing}
    tables: dict[str, dict] = {}
    missing: list[str] = []
    for key in KEYS:
        wanted_name = names["tables"][key]
        table_id = (configured or {}).get(key) or by_name.get(wanted_name)
        if not table_id:
            missing.append(key)
            continue
        tables[key] = {"id": table_id, "name": by_id.get(table_id) or wanted_name}
    return tables, missing


def _field_gaps(client, tables: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Contract field gaps for the tables that are present, plus read errors."""
    table_ids = {
        key: value.get("id")
        for key, value in (tables or {}).items()
        if value.get("id")
    }
    try:
        return kb.missing_required_fields(client, table_ids), []
    except Exception as exc:  # noqa: BLE001 - a read failure must not look ready
        return {}, [f"字段读回失败：{exc}"]


def _readiness_note(
    names: dict,
    missing: list[str],
    missing_fields: dict[str, list[str]],
    extra: list[str] | None = None,
) -> str:
    """One readable not-ready note shared by create/attach/status."""
    details: list[str] = []
    if missing:
        details.append("缺表：" + "、".join(names["tables"][key] for key in missing))
    if missing_fields:
        details.append(
            "缺字段："
            + "、".join(
                f"{names['tables'][key]}（{','.join(fields)}）"
                for key, fields in missing_fields.items()
            )
        )
    details.extend(extra or [])
    return "设施未就绪：" + "；".join(details)


def _folders_from_config(home: Path, client) -> dict:
    """Read folder tokens from the 配置 row without requiring a folder listing."""
    base = read_json(Path(home) / "knowledge" / "base.json") or {}
    configured = (base.get("tables") or {}).get("config")
    if not configured:
        return {}
    try:
        rows = client.list(configured)
    except Exception:  # noqa: BLE001 - reporting beats crashing
        return {}
    row = next((item for item in rows if str(item.get("fields", {}).get("键") or "") == "config"), None)
    if not row:
        return {}
    fields = row.get("fields") or {}
    return {
        "source": fields.get("原文目录 token") or "",
        "push": fields.get("推送目录 token") or "",
    }


def _record_ok(home: Path, evidence: dict, note: str) -> None:
    install_mod.record(home, "facilities", "ok", note=note, evidence=evidence, recheck=True)


def _record_blocked(home: Path, note: str, evidence: dict | None = None) -> None:
    install_mod.record(
        home, "facilities", "blocked", note=note, evidence=evidence or {}, recheck=True
    )


def _finish(
    home: Path,
    *,
    names: dict,
    base: dict,
    tables: dict,
    folders: dict,
    actions: list[str],
    reused: bool,
    missing: list[str] | None = None,
    missing_fields: dict[str, list[str]] | None = None,
    problems: list[str] | None = None,
) -> dict:
    missing = missing or []
    missing_fields = missing_fields or {}
    problems = problems or []
    payload = {
        "schema_version": 1,
        "names": names,
        "base": base,
        "tables": tables,
        "folders": folders,
        "actions": actions,
        "reused": reused,
        "missing": missing,
        "missing_fields": missing_fields,
        "problems": problems,
        "created_at": load_facilities(home).get("created_at") or now(),
    }
    save_facilities(home, payload)
    ok = not (missing or missing_fields or problems)
    if ok:
        _record_ok(
            home,
            {
                "base_token": base.get("token"),
                "base_name": base.get("name"),
                "tables": {key: value["id"] for key, value in tables.items()},
                "folders": folders,
            },
            f"设施已就绪：{base.get('name')}（{len(tables)} 表）",
        )
    else:
        note = _readiness_note(names, missing, missing_fields, problems)
        _record_blocked(
            home,
            note,
            {
                "missing": missing,
                "missing_fields": missing_fields,
                "base_token": base.get("token"),
            },
        )
    return {
        "ok": ok,
        **payload,
        "path": str(Path(home) / FACILITIES_REL),
    }


def _lock_recorded_names(record: dict, resolved: dict) -> tuple[dict, list[str]]:
    """Re-runs keep recorded display names; tables change names via ``rename``.

    A facilities record means those stable IDs and their current display names
    are already live.  Re-applying a changed name during ``create`` would make
    the creator look for a table that no longer exists by that name and build a
    duplicate, so recorded names win and the caller is told how to rename.
    """
    locked: list[str] = []
    if not record:
        return resolved, locked
    recorded_names = record.get("names") or {}
    recorded_base = recorded_names.get("base")
    if (record.get("base") or {}).get("token") and recorded_base:
        if resolved["base"] != recorded_base:
            locked.append(f"Base {resolved['base']} → {recorded_base}")
        resolved["base"] = recorded_base
    for key, entry in (record.get("tables") or {}).items():
        recorded_name = (entry or {}).get("name")
        if not recorded_name:
            continue
        if resolved["tables"].get(key) != recorded_name:
            locked.append(f"表 {key} {resolved['tables'].get(key)} → {recorded_name}")
        resolved["tables"][key] = recorded_name
    recorded_folders = recorded_names.get("folders") or {}
    for key in DEFAULT_FOLDERS:
        if recorded_folders.get(key):
            if resolved["folders"].get(key) != recorded_folders[key]:
                locked.append(
                    f"目录 {key} {resolved['folders'].get(key)} → {recorded_folders[key]}"
                )
            resolved["folders"][key] = recorded_folders[key]
    return resolved, locked


def create(
    home: Path,
    *,
    names: dict | None = None,
    confirmed: bool = False,
    admin=None,
    client=None,
    drive=None,
    cli_bin: str = "lark-cli",
    dry_run: bool = False,
    base_url: str = "",
) -> dict:
    """Create the Base and every facility, or reuse what is already recorded."""
    home = Path(home).expanduser().resolve()
    resolved = normalize(names or load_confirmed(home) or {})
    if not confirmed:
        return {
            "ok": False,
            "reason": "needs_user_confirmation",
            "plan": plan(home, resolved),
        }
    if dry_run:
        names_for_tables = resolved["tables"]
        return {
            "ok": True,
            "dry_run": True,
            "names": resolved,
            "actions": [
                f"create Base {resolved['base']}",
                "create tables: " + "、".join(names_for_tables.values()),
                "create folders: "
                + " / ".join(resolved["folders"].values()),
            ],
        }
    record = load_facilities(home)
    resolved, locked_names = _lock_recorded_names(record, resolved)
    confirm_names(home, resolved)

    admin = admin or LarkAdminClient(bin=cli_bin)
    drive = drive or LarkDrive(bin=cli_bin)
    actions: list[str] = []
    if locked_names:
        actions.append(
            "reuse recorded names for existing facilities（表改名请用 facilities rename）："
            + "；".join(locked_names)
        )
    base_config = read_json(home / "knowledge" / "base.json") or {}
    base_token = base_config.get("base_token") or ""
    reused = bool(base_token)
    base_info: dict = {}
    if not base_token and (record.get("base") or {}).get("token"):
        base_token = record["base"]["token"]
        kb.register_base(
            home,
            base_token,
            base_url=record["base"].get("url") or "",
            name=record["base"].get("name") or resolved["base"],
        )
        base_config = read_json(home / "knowledge" / "base.json") or {}
        reused = True
        actions.append("reuse Base from facilities record")

    existing_warning: dict | None = None
    if not base_token:
        # A same-name Base is reported, never adopted: the default name can
        # match a library the user already owns, and create must not touch it.
        existing_warning = _same_name_warning(admin, resolved)
        # A warning whose lookup failed has ``count: None``: it belongs in the
        # result, but it is not an action the creation actually performed.
        if existing_warning and existing_warning.get("count"):
            actions.append(existing_warning["note"])

    if not base_token:
        try:
            created = admin.create_base(
                resolved["base"],
                table_name=resolved["tables"]["cards"],
                fields=kb.KNOWLEDGE_SCHEMA["cards"],
            )
        except (AdminError, HostRunError) as exc:
            _record_blocked(home, f"创建 Base 失败：{exc}")
            result = {"ok": False, "reason": "base_create_failed", "note": str(exc)}
            if existing_warning:
                result["existing_same_name"] = existing_warning
            return result
        base_token = created["base_token"]
        base_info = {
            "token": base_token,
            "url": base_url or created.get("url") or f"https://feishu.cn/base/{base_token}",
            "name": resolved["base"],
        }
        kb.register_base(home, base_token, base_url=base_info["url"], name=base_info["name"])
        configured = {"cards": created.get("table_id")} if created.get("table_id") else {}
        path = home / "knowledge" / "base.json"
        config = read_json(path) or {}
        write_json(path, {**config, "tables": {**(config.get("tables") or {}), **configured}})
        actions.append(f"create Base {resolved['base']}")
        reused = False

    if not base_info:
        base_info = {
            "token": base_token,
            "url": base_url or base_config.get("base_url") or f"https://feishu.cn/base/{base_token}",
            "name": base_config.get("name") or resolved["base"],
        }

    client = _client_for(home, client, cli_bin=cli_bin)
    combined_names = {
        **resolved["tables"],
        # library.setup keeps the singular historical keys; pass both so a
        # customized 原文/推送日志/决定 display name is honored.
        "source": resolved["tables"]["sources"],
        "push": resolved["tables"]["pushes"],
        "decision": resolved["tables"]["decisions"],
        "root_folder": resolved["folders"]["root"],
        "source_folder": resolved["folders"]["source"],
        "push_folder": resolved["folders"]["push"],
    }
    try:
        created = kb.create_tables(
            home, client=client, drive=drive, names=combined_names
        )
    except Exception as exc:  # noqa: BLE001 - report partial progress and keep IDs
        actions.append(f"partial: {exc}")
        config = read_json(home / "knowledge" / "base.json") or {}
        tables, missing = _tables_snapshot(client, config.get("tables") or {}, resolved)
        missing_fields, field_problems = _field_gaps(client, tables)
        _record_blocked(
            home,
            f"创建表/目录中断：{exc}",
            {"actions": actions, "missing": missing, "missing_fields": missing_fields},
        )
        result = {
            "ok": False,
            "reason": "facility_create_partial",
            "note": str(exc),
            "base": base_info,
            "tables": tables,
            "missing": missing,
            "missing_fields": missing_fields,
            "problems": field_problems,
            "actions": actions,
        }
        if existing_warning:
            result["existing_same_name"] = existing_warning
        return result
    actions.extend(created.get("actions") or [])
    config = read_json(home / "knowledge" / "base.json") or {}
    tables, missing = _tables_snapshot(client, config.get("tables") or {}, resolved)
    missing_fields, field_problems = _field_gaps(client, tables)
    library_report = created.get("library") or {}
    folders = {
        "root": "",
        "source": library_report.get("source_folder") or "",
        "push": library_report.get("push_folder") or "",
    }
    if not folders["source"] or not folders["push"]:
        folders = {**folders, **_folders_from_config(home, client)}
    result = _finish(
        home,
        names=resolved,
        base=base_info,
        tables=tables,
        folders=folders,
        actions=actions,
        reused=reused,
        missing=missing,
        missing_fields=missing_fields,
        problems=field_problems,
    )
    if existing_warning:
        result["existing_same_name"] = existing_warning
    return result


def attach(
    home: Path,
    link: str,
    *,
    names: dict | None = None,
    confirmed: bool = False,
    client=None,
    drive=None,
    cli_bin: str = "lark-cli",
    create_missing: bool = False,
) -> dict:
    """Connect an existing Base by link; inspect first, create only if asked."""
    home = Path(home).expanduser().resolve()
    resolved = normalize(names or load_confirmed(home) or {})
    if not confirmed:
        return {"ok": False, "reason": "needs_user_confirmation", "plan": plan(home, resolved)}
    confirm_names(home, resolved)
    client = client
    try:
        attached = kb.attach(home, link, client=client)
    except HostRunError as exc:
        _record_blocked(home, f"接入失败：{exc}")
        return {"ok": False, "reason": "attach_failed", "note": str(exc)}
    if client is None:
        client = base_client(home)
    actions = ["attach Base " + attached.get("base_token", "")]
    combined_names = {
        **resolved["tables"],
        # library.setup keeps the singular historical keys; pass both so a
        # customized 原文/推送日志/决定 display name is honored.
        "source": resolved["tables"]["sources"],
        "push": resolved["tables"]["pushes"],
        "decision": resolved["tables"]["decisions"],
        "root_folder": resolved["folders"]["root"],
        "source_folder": resolved["folders"]["source"],
        "push_folder": resolved["folders"]["push"],
    }
    if create_missing and attached.get("missing"):
        created = kb.create_tables(home, client=client, drive=drive, names=combined_names)
        actions.extend(created.get("actions") or [])
    config = read_json(home / "knowledge" / "base.json") or {}
    tables, missing = _tables_snapshot(client, config.get("tables") or {}, resolved)
    missing_fields, field_problems = _field_gaps(client, tables)
    base_info = {
        "token": config.get("base_token"),
        "url": config.get("base_url") or "",
        "name": config.get("name") or resolved["base"],
    }
    folders = _folders_from_config(home, client)
    result = _finish(
        home,
        names=resolved,
        base=base_info,
        tables=tables,
        folders=folders,
        actions=actions,
        reused=True,
        missing=missing,
        missing_fields=missing_fields,
        problems=field_problems,
    )
    result["attached"] = True
    result["has_user_data"] = attached.get("has_user_data")
    return result


def status(home: Path, *, client=None) -> dict:
    home = Path(home).expanduser().resolve()
    record = load_facilities(home)
    config = read_json(home / "knowledge" / "base.json") or {}
    if not record and not config.get("base_token"):
        return {"ok": False, "status": "pending", "note": "尚未创建或接入设施"}
    names = record.get("names") or normalize()
    if client is None:
        try:
            client = base_client(home)
        except HostRunError as exc:
            return {"ok": False, "status": "blocked", "note": str(exc), "names": names}
    try:
        tables, missing = _tables_snapshot(client, config.get("tables") or {}, names)
    except Exception as exc:  # noqa: BLE001 - permission/network problems are results
        return {"ok": False, "status": "blocked", "note": f"读取设施失败：{exc}", "names": names}
    missing_fields, field_problems = _field_gaps(client, tables)
    folders = {**(_folders_from_config(home, client) or {}), **(record.get("folders") or {})}
    missing_folders = [key for key in ("source", "push") if not folders.get(key)]
    try:
        has_user_data = bool(
            sum(
                len(client.list(value["id"]))
                for key, value in tables.items()
                if key in ("cards", "propositions", "papers", "evidence")
            )
        ) if tables else False
    except Exception:  # noqa: BLE001 - a read failure is not "empty"
        has_user_data = None
    ok = not missing and not missing_fields and not missing_folders and not field_problems
    if ok:
        note = "设施结构完整"
    else:
        extra = (
            ["缺云盘目录：" + "、".join(missing_folders)] if missing_folders else []
        ) + list(field_problems)
        note = _readiness_note(names, missing, missing_fields, extra)
    return {
        "ok": ok,
        "status": "ok" if ok else "blocked",
        "note": note,
        "base": {**(record.get("base") or {}), "token": config.get("base_token")},
        "tables": tables,
        "missing": missing,
        "missing_fields": missing_fields,
        "folders": folders,
        "missing_folders": missing_folders,
        "names": names,
        "has_user_data": has_user_data,
    }


def rename(home: Path, key: str, new_name: str, *, client=None) -> dict:
    """Rename one table's display name; the stable ID keeps working."""
    home = Path(home).expanduser().resolve()
    record = load_facilities(home)
    if key not in KEYS or key not in (record.get("tables") or {}):
        raise HostRunError(f"未知或尚未创建的表：{key}")
    table_id = record["tables"][key]["id"]
    normal = _check_name(new_name, "表")
    client = client or base_client(home)
    client.rename_table(table_id, normal)
    record["tables"][key]["name"] = normal
    record["names"]["tables"][key] = normal
    save_facilities(home, record)
    return {"ok": True, "key": key, "id": table_id, "name": normal}
