"""Lark CLI: discover or privately install it, then check identity and access.

The three facts are reported separately, because none of them implies another:

    executable   the CLI file exists and answers
    identity     a user and/or bot token is actually logged in
    access       the granted scopes *and* real read operations on Base/Drive

Installation never delegates to a global npm install and never writes PATH or
the registry: a missing CLI is installed under ``<root>/.horizon`` and wrapped
by a small launcher whose absolute path is recorded.  A login is interactive,
so this module only prepares the exact command, then re-checks; it never stores
or prints a credential.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from common import ensure_dir, now
from hostrun.base import HostRunError, base_config, resolve_executable, run_json

SCHEMA_VERSION = 1
CLI_NAMES = ["lark-cli", "lark-cli.cmd", "lark-cli.exe", "lark-cli.bat"]
# Current lark-cli grants granular scopes; older deployments granted one broad
# scope per domain.  Requiring the granular names would mislabel a working
# installation as blocked, so a broad grant still covers the narrow names.
REQUIRED_SCOPES = {
    "base": [
        "base:app:read",
        "base:table:read",
        "base:field:read",
        "base:record:read",
        "base:record:create",
        "base:record:update",
        "base:record:delete",
    ],
    "drive": [
        "drive:file:upload",
        "drive:file:download",
        "space:folder:create",
        "space:document:delete",
    ],
    "messages": ["im:message"],
}
LEGACY_BROAD_SCOPES = {
    "bitable:app": ("base:",),
    "drive:drive": ("drive:", "space:"),
    "im:message:send_as_bot": ("im:message",),
}
READY_STATUSES = {"valid", "ready"}
REFRESHABLE_STATUSES = {"needs_refresh"}
#: The Feishu developer console, where bot scopes/app permissions are configured.
#: The console prints this link when a scope or the bot identity is missing.
DEVELOPER_CONSOLE_URL = "https://open.feishu.cn/app"
SCOPE_KEYS = (
    "scopes",
    "scope",
    "grantedScopes",
    "granted_scopes",
    "permissions",
    "authorizedScopes",
)


# --------------------------------------------------------------------- helpers
def _run(argv: list[str], *, runner=subprocess.run, timeout: int = 120) -> dict:
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
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "out": (proc.stdout or "").strip(),
        "err": (proc.stderr or "").strip(),
    }


def _tail(text: str, limit: int = 400) -> str:
    return " ".join((text or "").split())[-limit:]


def _refresh_window_open(value) -> bool:
    """Whether a reported refresh expiry is still in the future.

    A missing timestamp counts as open: the CLI only reports it for a logged-in
    identity, and treating an absent field as expired would hide a working
    installation.  A present timestamp decides the answer.
    """
    if not value:
        return True
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment > datetime.now(timezone.utc)


def private_bin(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / ".horizon" / "bin"


STALE_CLI_GUIDANCE = (
    "请重装 @larksuite/cli，并用 `lark discover --cli <可执行文件绝对路径>` 记录新路径；"
    "不会降级到 PATH 中的其他 lark-cli"
)


def explicit_cli_path(spec: str | None) -> Path | None:
    """An explicit or recorded CLI path, as opposed to a bare command name.

    ADR 0007 records the executable actually in use.  A path that no longer
    exists is stale, not a hint to look somewhere else.
    """
    if not spec:
        return None
    candidate = Path(str(spec)).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        return candidate
    return None


def resolve_cli(
    spec: str | None,
    *,
    private_dirs: list[Path] | None = None,
    names: list[str] | None = None,
) -> dict:
    """Resolve the CLI; a recorded absolute path is authoritative.

    When ``spec`` is a path (explicit ``--cli`` or the recorded
    ``paths.lark_cli``) and it no longer exists, the answer is ``blocked`` with
    rediscovery guidance — never a silent fallback to a different ``lark-cli``
    found on PATH (ADR 0007).
    """
    explicit = explicit_cli_path(spec)
    if explicit is not None:
        if explicit.is_file():
            return {"found": True, "path": str(explicit.resolve()), "source": "explicit"}
        return {
            "found": False,
            "path": str(explicit),
            "source": "explicit",
            "stale": True,
            "note": f"记录的飞书 CLI 路径已失效：{explicit}；{STALE_CLI_GUIDANCE}",
        }
    return resolve_executable(
        spec, private_dirs=list(private_dirs or []), names=names or CLI_NAMES
    )


def discover_cli(root: str | Path, *, spec: str | None = None, home: str | Path | None = None) -> dict:
    """Resolve the CLI from explicit config, private directories, then PATH.

    A recorded absolute path that no longer exists is *stale* and blocks with
    rediscovery guidance; it never falls back to a different CLI on PATH.
    """
    root = Path(root).expanduser().resolve()
    # ``.horizon/bin`` is where a private install writes its launcher;
    # ``.horizon/lark-cli/bin`` is where a migrated private CLI lives (#76).
    private_dirs = [root / "agent" / "node_modules" / "@larksuite" / "cli" / "bin",
                    root / ".horizon" / "bin", root / ".horizon" / "lark-cli" / "bin"]
    if home:
        private_dirs.extend([Path(home) / "bin", Path(home) / "lark-cli" / "bin"])
    found = resolve_cli(spec, private_dirs=private_dirs, names=CLI_NAMES)
    result = {"ok": bool(found["found"]), **found}
    if not result["ok"]:
        result.setdefault(
            "note",
            "未找到飞书 CLI；把可执行文件装好后用 `lark discover --cli <绝对路径>` 记录，"
            "或运行 `lark install` 私有安装",
        )
    return result


def _launcher(binary: Path, node: Path | None, *, target: Path) -> Path:
    ensure_dir(target.parent)
    if os.name == "nt":
        target = target.with_suffix(".cmd")
        if node is not None and binary.suffix in (".js", ".mjs"):
            body = f'@echo off\r\n"{node}" "{binary}" %*\r\n'
        else:
            body = f'@echo off\r\n"{binary}" %*\r\n'
        target.write_text(body, encoding="utf-8", newline="")
        return target
    if node is not None and binary.suffix in (".js", ".mjs"):
        body = f'#!/bin/sh\nexec "{node}" "{binary}" "$@"\n'
    else:
        body = f'#!/bin/sh\nexec "{binary}" "$@"\n'
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)
    return target


def _find_package_entry(prefix: Path) -> Path | None:
    for rel in (
        "node_modules/@larksuite/cli/bin/lark-cli.js",
        "node_modules/@larksuite/cli/bin/lark-cli",
        "node_modules/@larksuite/cli/index.js",
    ):
        candidate = prefix / rel
        if candidate.is_file():
            return candidate
    return None


def install_cli(
    root: str | Path,
    *,
    runner=subprocess.run,
    registry: str | None = None,
) -> dict:
    """Install @larksuite/cli under the workspace and expose a private wrapper."""
    root = Path(root).expanduser().resolve()
    npm = resolve_executable(None, private_dirs=[root / ".horizon" / "node"], names=["npm", "npm.cmd"])
    if not npm["found"]:
        return {
            "ok": False,
            "reason": "npm_missing",
            "note": "未找到 npm。请先让宿主安装 Node.js/npm，再重试；未修改 PATH 或全局包",
        }
    node = resolve_executable(None, names=["node", "node.exe"])
    prefix = root / ".horizon" / "lark"
    argv = [
        npm["path"],
        "install",
        "--prefix",
        str(prefix),
        "--no-fund",
        "--no-audit",
        "--loglevel",
        "error",
    ]
    if registry:
        argv += ["--registry", registry]
    argv.append("@larksuite/cli")
    installed = _run(argv, runner=runner, timeout=900)
    if not installed["ok"]:
        return {
            "ok": False,
            "reason": "npm_install_failed",
            "note": "npm 私有安装失败：" + (_tail(installed["err"] or installed["out"]) or "退出非零"),
            "prefix": str(prefix),
        }
    entry = _find_package_entry(prefix)
    if entry is None:
        shim = prefix / "node_modules" / ".bin" / ("lark-cli.cmd" if os.name == "nt" else "lark-cli")
        if not shim.is_file():
            return {
                "ok": False,
                "reason": "cli_entry_missing",
                "note": "npm 安装完成但找不到 lark-cli 入口；保留进度，可重试或手动指定路径",
                "prefix": str(prefix),
            }
        entry = shim
    launcher = _launcher(
        entry,
        Path(node["path"]) if node["found"] else None,
        target=root / ".horizon" / "bin" / "lark-cli",
    )
    version = _run([str(launcher), "--version"], runner=runner, timeout=120)
    return {
        "ok": True,
        "path": str(launcher),
        "entry": str(entry),
        "source": "private",
        "version": version["out"].splitlines()[0] if version["ok"] and version["out"] else "",
        "evidence": _tail(version["err"] or version["out"]) if not version["ok"] else "",
    }


# --------------------------------------------------------------------- identity
def _dpop_field(payload: dict, identities: dict, *keys: str) -> str:
    """A non-secret DPoP status word from whichever level reports it."""
    sources = (
        payload,
        identities,
        identities.get("user") or {},
        identities.get("bot") or {},
    )
    for key in keys:
        for source in sources:
            value = source.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, (dict, list, tuple)):
                # Never flatten a structured payload into the report.
                continue
            return str(value)
    return ""


def parse_auth(payload: dict) -> dict:
    """Booleans and status words only — the payload may carry tokens."""
    identities = (payload or {}).get("identities") or {}
    user = identities.get("user") or {}
    bot = identities.get("bot") or {}

    def facts(entry: dict) -> dict:
        status = str(entry.get("tokenStatus") or entry.get("status") or "").lower()
        refresh_at = entry.get("refreshExpiresAt") or entry.get("refresh_expires_at")
        refreshable = _refresh_window_open(refresh_at)
        available = (
            bool(entry.get("available"))
            if "available" in entry
            else status in READY_STATUSES | REFRESHABLE_STATUSES
        )
        # `needs_refresh` is not a failure: the CLI renews the access token on
        # the next user call, and only a closed refresh window makes it expire.
        ready = available and (
            status in READY_STATUSES
            or (status in REFRESHABLE_STATUSES and refreshable)
        )
        return {
            "available": available,
            "status": status,
            "ready": ready,
            "expired": status in {"expired", "invalid"}
            or (status in REFRESHABLE_STATUSES and not refreshable),
            "refreshable": refreshable,
        }

    user_facts, bot_facts = facts(user), facts(bot)
    return {
        "user": user_facts,
        "bot": bot_facts,
        "user_open_id_present": bool(user.get("userOpenId") or user.get("openId")),
        "dpop_key_status": _dpop_field(
            payload or {}, identities, "dpopKeyStatus", "dpop_key_status"
        ),
        "dpop_mode": _dpop_field(payload or {}, identities, "dpopMode", "dpop_mode"),
        "tenant_matches": (identities.get("tenant") or {}).get("matched"),
        "scopes": _extract_scopes(payload),
    }


def _extract_scopes(payload: dict) -> list[str]:
    for key in SCOPE_KEYS:
        value = (payload or {}).get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
        if isinstance(value, str) and value.strip():
            return [part for part in re.split(r"[\s,]+", value.strip()) if part]
        if isinstance(value, dict):
            flat = [name for name, granted in value.items() if granted]
            if flat:
                return flat
    identities = (payload or {}).get("identities") or {}
    for side in ("user", "bot"):
        entry = identities.get(side) or {}
        for key in SCOPE_KEYS:
            value = entry.get(key)
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                return list(value)
            if isinstance(value, str) and value.strip():
                return [part for part in re.split(r"[\s,]+", value.strip()) if part]
    return []


def identity(cli: str | None, *, runner=subprocess.run, timeout: int = 30) -> dict:
    if not cli:
        return {"status": "blocked", "note": "缺少 lark-cli 路径", "evidence": {}}
    result = run_json([cli, "auth", "status", "--json", "--verify"], runner=runner, timeout=timeout)
    if not result.get("ok"):
        return {
            "status": "blocked",
            "note": f"auth status 无法读取：{result.get('error')}",
            "evidence": {"expired": False},
        }
    facts = parse_auth(result["payload"])
    ready = [name for name in ("user", "bot") if facts[name]["ready"]]
    missing = [name for name in ("user", "bot") if not facts[name]["ready"]]
    stale = [
        name
        for name in ("user", "bot")
        if facts[name]["ready"] and facts[name]["status"] in REFRESHABLE_STATUSES
    ]
    note = (
        "user 与 bot 身份均可用"
        if not missing
        else "需要用户完成飞书登录/授权：" + "、".join(missing)
    )
    if stale and not missing:
        note += "（" + "、".join(stale) + " 的访问令牌将在下次调用时自动刷新）"
    return {
        "status": "ok" if not missing else "blocked",
        "note": note,
        "evidence": {
            "ready": ready,
            "missing": missing,
            "expired": any(facts[name]["expired"] for name in ("user", "bot")),
            "refresh_pending": stale,
            "tenant_matches": facts["tenant_matches"],
            "dpop_key_status": facts["dpop_key_status"],
            "dpop_mode": facts["dpop_mode"],
            "scope_count": len(facts["scopes"]),
        },
        "scopes": facts["scopes"],
    }


def user_open_id(cli: str | None, *, runner=subprocess.run, timeout: int = 30) -> dict:
    """The authenticated *user's* open id, for recipient verification.

    Returned to the calling host agent only; reports and identity evidence keep
    booleans, never the raw id.  A cached id alone is not evidence the identity
    is usable: the CLI keeps reporting ``identities.user.openId`` while the user
    token is missing or unrefreshable.  Recipient verification must therefore
    use the same readiness rule as the identity preflight, otherwise a stale id
    would satisfy a gate that the spec says must return ``recipient_unverified``.
    """
    if not cli:
        return {"ok": False, "open_id": ""}
    result = run_json(
        [cli, "auth", "status", "--json", "--verify"], runner=runner, timeout=timeout
    )
    if not result.get("ok"):
        return {"ok": False, "open_id": ""}
    payload = result.get("payload") or {}
    if not parse_auth(payload)["user"]["ready"]:
        return {"ok": False, "open_id": ""}
    identities = payload.get("identities") or {}
    user = identities.get("user") or {}
    open_id = str(user.get("openId") or user.get("userOpenId") or "")
    return {"ok": bool(open_id), "open_id": open_id}


def _scope_covered(required: str, granted: str | set[str]) -> bool:
    """A requirement holds when granted, or when a legacy broad grant covers it."""
    present = {granted} if isinstance(granted, str) else set(granted)
    if required in present:
        return True
    for broad, prefixes in LEGACY_BROAD_SCOPES.items():
        if broad in present and required.startswith(tuple(prefixes)):
            return True
        if required == broad and any(item.startswith(tuple(prefixes)) for item in present):
            return True
    return False


def authorization(
    scopes: list[str],
    *,
    required: list[str] | None = None,
    source: str = "auth status",
) -> dict:
    """Compare declared required scopes with what the CLI reports.

    Without a scope list the honest answer is ``untested``: a successful login
    does not prove any permission.
    """
    required = list(required or [])
    if not scopes:
        return {
            "status": "untested",
            "note": "登录状态未报告授权范围；不能据此声明权限齐全",
            "required": required,
            "granted": [],
            "missing": [],
            "source": source,
        }
    granted = sorted({str(item) for item in scopes})
    present = set(granted)
    if required:
        missing = [scope for scope in required if not _scope_covered(scope, present)]
        broad_only = [
            scope
            for scope in required
            if scope not in present and _scope_covered(scope, present)
        ]
        status = "ok" if not missing else "blocked"
        note = (
            "所需授权范围已具备"
            if not missing
            else "缺少授权范围：" + "、".join(missing)
        )
        if broad_only and not missing:
            note += "（由更宽的历史授权覆盖：" + "、".join(broad_only) + "）"
    else:
        missing = []
        broad_only = []
        status = "ok"
        note = "已取得授权范围列表；具体资源访问另行验证"
    return {
        "status": status,
        "note": note,
        "required": required,
        "granted": granted,
        "missing": missing,
        "source": source,
    }


# -------------------------------------------------------------------- resources
def _call(cli: str, args: list[str], *, runner=subprocess.run) -> dict:
    result = _run([cli, *args, "--format", "json"], runner=runner, timeout=120)
    if not result["ok"]:
        return {"ok": False, "error": _tail(result["err"] or result["out"]) or f"exit {result['code']}"}
    try:
        payload = json.loads(result["out"] or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "non-JSON output"}
    if payload.get("ok") is False:
        return {"ok": False, "error": _tail(json.dumps(payload.get("error") or payload, ensure_ascii=False))}
    return {"ok": True, "data": payload.get("data") or {}}


def resource_preflight(
    home: str | Path,
    cli: str | None,
    *,
    base_token: str | None = None,
    folders: list[str] | None = None,
    runner=subprocess.run,
) -> dict:
    """Read-only proof for Base and Drive; messages stay for the real loop.

    Explicit tokens let an existing library be checked without attaching it:
    this function only issues read calls and never records or rewrites a
    facility.
    """
    home = Path(home)
    if not cli:
        return {
            "status": "blocked",
            "note": "缺少 lark-cli 路径",
            "base": {"status": "blocked"},
            "drive": {"status": "blocked"},
            "messages": {"status": "untested", "note": "真实消息发送由临时闭环验收"},
        }
    if base_token is None:
        try:
            base_token = base_config(home).get("base_token")
        except HostRunError:
            base_token = None
    checks = {}
    # ``untested`` means there are no resource tokens to probe yet, not that
    # permissions passed: #53 stays blocked until a real Base/Drive read succeeds.
    if not base_token:
        checks["base"] = {"status": "untested", "note": "设施尚未创建或接入"}
    else:
        result = _call(
            cli,
            ["base", "+table-list", "--as", "user", "--base-token", base_token],
            runner=runner,
        )
        checks["base"] = {
            "status": "ok" if result["ok"] else "blocked",
            "note": "可读取 Base 表结构" if result["ok"] else "Base 读取失败：" + result["error"],
        }
    folder_list = list(folders or [])
    if not folder_list:
        checks["drive"] = {"status": "untested", "note": "云盘目录尚未创建或接入"}
    else:
        failures = []
        for folder in folder_list:
            result = _call(
                cli,
                [
                    "drive",
                    "files",
                    "list",
                    "--as",
                    "user",
                    "--params",
                    json.dumps(
                        {"folder_token": folder, "page_size": 10}, ensure_ascii=False
                    ),
                ],
                runner=runner,
            )
            if not result["ok"]:
                failures.append(result["error"])
        checks["drive"] = {
            "status": "ok" if not failures else "blocked",
            "note": "可读取原文库与推送库目录" if not failures else "云盘读取失败：" + "；".join(failures),
        }
    checks["messages"] = {
        "status": "untested",
        "note": "发送权限与真实投递由临时闭环验证，不用登录状态代替",
    }
    statuses = {checks["base"]["status"], checks["drive"]["status"]}
    if "blocked" in statuses:
        status = "blocked"
    elif statuses == {"ok"}:
        # Real Base + Drive reads are the permission proof; messages stay a
        # separate, untested sub-fact until the temporary loop proves delivery.
        status = "ok"
    else:
        status = "untested"
    note = (
        "Base 与 Drive 读取已证明；消息投递由临时闭环单独验收"
        if status == "ok"
        else "资源级证据见各分项"
    )
    return {"status": status, "note": note, **checks}


# ----------------------------------------------------------------------- driver
def status(
    root: str | Path,
    *,
    home: str | Path | None = None,
    required: list[str] | None = None,
    runner=subprocess.run,
    spec: str | None = None,
    base_token: str | None = None,
    folders: list[str] | None = None,
) -> dict:
    root = Path(root).expanduser().resolve()
    found = discover_cli(root, spec=spec, home=home)
    if not found["ok"]:
        note = found.get("note") or "未找到 lark-cli"
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "cli": {"status": "blocked", "note": note, **found},
            "identity": {"status": "blocked"},
            "authorization": {"status": "blocked"},
            "resources": {"status": "blocked"},
            "next": (
                f"python -m hostrun.cli lark discover --cli <lark-cli 绝对路径> "
                f"--root {root}；或 python -m hostrun.cli lark install --root {root}"
            ),
        }
    identity_result = identity(found["path"], runner=runner)
    authz = authorization(
        identity_result.get("scopes") or [], required=required
    )
    home_path = Path(home).expanduser().resolve() if home else None
    resource_base = base_token
    resource_folders = list(folders or [])
    if home_path:
        from hostrun import facilities as facilities_mod

        record = facilities_mod.load_facilities(home_path)
        recorded_folders = record.get("folders") or {}
        if not resource_base:
            resource_base = (record.get("base") or {}).get("token")
        if not resource_folders:
            resource_folders = [
                folder
                for folder in (recorded_folders.get("source"), recorded_folders.get("push"))
                if folder
            ]
    if home_path or resource_base or resource_folders:
        # Explicit tokens probe an existing library read-only; nothing is
        # attached or written back.
        resources = resource_preflight(
            home_path or Path(root),
            found["path"],
            base_token=resource_base,
            folders=resource_folders,
            runner=runner,
        )
    else:
        resources = {"status": "untested", "note": "未提供运行时区，跳过资源预检"}
    ok = (
        identity_result["status"] == "ok"
        and authz["status"] == "ok"
        and resources["status"] == "ok"
    )
    next_step = None
    if identity_result["status"] != "ok":
        next_step = f"python -m hostrun.cli lark login --root {root}"
    elif authz["status"] == "blocked":
        next_step = "在飞书管理后台补齐授权范围，然后重试 lark status"
    elif resources["status"] == "blocked":
        next_step = "检查设施链接、身份租户与资源权限"
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "cli": {"status": "ok", "path": found["path"], "source": found["source"]},
        "identity": identity_result,
        "authorization": authz,
        "resources": resources,
        "next": next_step,
        "checked_at": now(),
    }


def login_command(cli: str | None, *, identity_name: str = "user") -> dict:
    """Prepare the real split-flow login; this module never fakes a login.

    ``lark-cli auth login`` must carry ``--domain`` / ``--scope`` / ``--recommend``
    and ``--no-wait --json`` so the host can show the URL and finish later with
    the returned device code.  Bot scopes are configured in the developer
    console; ``auth login`` is never used for a bot.
    """
    if not cli:
        return {"ok": False, "note": "先安装或指定 lark-cli"}
    if identity_name == "bot":
        return {
            "ok": False,
            "status": "action_required",
            "steps": [
                "在飞书开发者后台为应用开通 bot scope（不要对 bot 执行 auth login）",
                "用错误响应里的 console_url 直达后台；完成后重跑 lark status",
            ],
            "storage": "应用凭证由 lark-cli 的 config init 保存；horizon 不读取、不复制",
        }
    command = f'"{cli}" auth login --domain all --no-wait --json'
    return {
        "ok": True,
        "status": "action_required",
        "command": command,
        "steps": [
            "首次使用先运行 `lark-cli config init` 完成应用配置（若 CLI 已配置则跳过）",
            "运行上面的命令；从 JSON 读取 verification_url 与 device_code",
            "把 verification_url 原样发给用户，并用 `lark-cli auth qrcode <url> --output <png>` 附二维码",
            "用户完成授权后，由你运行 `lark-cli auth login --device-code <device_code>`",
            "完成后运行 hostrun lark status 复核；未复核前不声明权限就绪",
        ],
        "storage": "凭据只保存在 lark-cli 自己的机制里；horizon 不读取、不复制 token",
    }


def _json_object(text: str) -> dict | None:
    """The first JSON object in a CLI answer, tolerating a log line around it."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


def _field(payload: dict, *names: str) -> str:
    """A non-secret display field from a flat payload or its ``data`` object."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for source in (payload, data):
        for name in names:
            value = source.get(name)
            if value not in (None, ""):
                return str(value)
    return ""


def request_login(cli: str | None, *, runner=subprocess.run, timeout: int = 60) -> dict:
    """Ask for a device-code login: the verification URL and the device code.

    The split flow is ``auth login --domain all --no-wait --json``: it returns
    the URL the user opens and the code that finishes the login.  Nothing is
    stored or printed by this module; the caller shows the two values and then
    calls :func:`complete_login`.  A failed request never invents a code.
    """
    if not cli:
        return {"ok": False, "note": "先安装或指定 lark-cli"}
    result = _run(
        [cli, "auth", "login", "--domain", "all", "--no-wait", "--json"],
        runner=runner,
        timeout=timeout,
    )
    if not result["ok"]:
        return {
            "ok": False,
            "note": "无法取得登录链接与设备码：" + (_tail(result["err"] or result["out"]) or "退出非零"),
        }
    payload = _json_object(result["out"])
    if payload is None:
        return {"ok": False, "note": "登录命令未返回 JSON；无法取得登录链接与设备码"}
    url = _field(payload, "verification_url", "verificationUrl", "verification_uri")
    code = _field(payload, "device_code", "deviceCode")
    if not url or not code:
        return {"ok": False, "note": "登录响应缺少 verification_url 或 device_code"}
    expires = _field(payload, "expires_in", "expiresIn")
    return {
        "ok": True,
        "verification_url": url,
        "device_code": code,
        "expires_in": int(expires) if expires.isdigit() else 0,
    }


def complete_login(
    cli: str,
    device_code: str,
    *,
    runner=subprocess.run,
    timeout: int = 300,
) -> dict:
    if not cli or not device_code:
        return {"ok": False, "note": "需要 CLI 路径与 device_code"}
    result = _run(
        [cli, "auth", "login", "--device-code", device_code],
        runner=runner,
        timeout=timeout,
    )
    return {
        "ok": result["ok"],
        "exit": result["code"],
        "note": "" if result["ok"] else _tail(result["err"] or result["out"]),
    }
