"""Console ``/horizon-init`` step machine (ADR 0009 §7).

The initialization is an ordered, resumable state machine executed inside the
Console.  This module delivers the seven ADR §7 steps:

1. **archive directory** — confirm the default archive (``Y``/Enter) or open the
   system folder dialog (``N``); a cancelled dialog falls back to the default.
   A directory outside the code directory is accepted only after a real
   create/delete write probe, and a failed probe tells the user to restart the
   Console with full permissions while keeping progress.
2. **model configuration** — use Horizon's bundled Pi Agent; collect the
   provider, model and API key locally, preserving existing Feishu setup.
3. **Lark CLI** — the three-level detection of ADR 0009 §7: an already
   configured, working CLI is reused (ADR 0007); a CLI installed elsewhere is
   adopted only after an explicit confirmation, and a missing one is privately
   installed into the code directory's runtime area; a CLI that is present but
   not logged in runs the split device-code login inside the Console (the
   verification link and device code are printed, the login is polled, and the
   flow continues automatically); a logged-in CLI with a scope gap prints the
   Feishu developer-console link and the missing list and waits for a re-check.
   The login wait is capped at 15 minutes, a failure offers "retry / abort
   (progress preserved)" and never a skip, and no system terminal is opened.
4. **Transport test** — one test HTML message and one single-button card are
   sent to the authenticated user; the step waits for the card callback and
   compares the clicker with the logged-in open id.  A matching click writes
   ``install/recipient.json`` (the record the Feishu owner gate reads) and
   records the step; no click, a timeout or another person's click leaves the
   step unrecorded and offers retry/abort.
5. **Facility naming and creation** — the three user-visible names (Base, source
   Drive folder, push Drive folder) with Enter = default and typed input as an
   override; table names stay fixed and are never asked.  Invalid characters and
   duplicate names are rejected and the same name is asked again.  An existing
   Base with the same display name triggers reuse-or-create: reuse attaches the
   existing Base (no duplicate is created), create builds a new one.  The Base,
   every table and the two Drive folders are created automatically and their
   stable IDs are recorded in ``install/facilities.json`` and the step state.
6. **Daily push time** — one daily slot in the user's local timezone, default
   ``08:00``, validated and persisted through
   :func:`hostrun.console_service.set_push_time` (the #81 clock, no host
   scheduler import), which also records the next occurrence.
7. **Knowledge-base gate** — "初始化知识库" hands the console over to
   ``/horizon-update-kb``; "暂不初始化" records an explicit closed gate, starts
   the service and tells the user that the daily push is blocked until the
   knowledge base exists.  A completed ``/horizon-update-kb`` review releases
   the gate automatically (:mod:`hostrun.console_gate`).

Initialization completes by applying the archive directory confirmed in step 1
(``layout.ensure`` + ``layout.remember``), recording a completion state, and
starting the service; an already-complete initialization is reported without
repeating any side effect.

State lives in the code directory's runtime area at
``<runtime>/install/initial/state.json`` — the ``layout`` category ``init``.
It is deliberately a **separate** file from ``install.STEPS``: installation
probes and console initialization are different state machines (other tests
assert ``install.STEPS``), and the knowledge-card initialization
(:mod:`hostrun.initialize`) already uses sibling files in the same directory.
Every completed step is persisted immediately, so an interruption resumes at the
first unfinished step and never leaves a half-created facility behind.

Everything interactive is injectable (``reader``, ``out``, ``isatty``,
``pick_folder``, ``write_probe``, ``lark_runner``, ``lark_install``, ``clock``,
``sleep``), so the whole flow is offline-testable.  The real Windows folder
dialog, a real folder outside the DSH workspace, a real device-code login and
the real browser authorization stay ``untested`` until recorded live.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

from common import RUNTIME_DIRNAME, ensure_dir, now, read_json, write_json
from hostrun import console_gate, console_service, facilities as facilities_mod
from hostrun import lark, layout
from hostrun.base import HostRunError, load_config, update_config

#: ``layout`` already declares ``install/initial`` as the code-directory
#: initialization-state category (#76); the console's step state is one file in
#: it, next to — never inside — ``install.STEPS``.
STATE_REL = layout.CATEGORY_RELS["init"] / "state.json"
SCHEMA_VERSION = 1

#: ADR 0009 §7: the login wait is capped at 15 minutes and re-checked every 5 s.
LARK_WAIT_SECONDS = 15 * 60
LARK_POLL_SECONDS = 5
#: One ``auth login --device-code`` call may block until the user authorizes;
#: a poll never asks for more than this at once.
LARK_COMPLETE_TIMEOUT = 300
LARK_REQUIRED_SCOPES = tuple(
    dict.fromkeys(scope for scopes in lark.REQUIRED_SCOPES.values() for scope in scopes)
)

#: The transport test (#86): one HTML message plus one single-button card.  The
#: owner clicks the button; the callback must arrive through the card event
#: stream.  ``recipient.json`` is the same record the Feishu owner gate reads.
TRANSPORT_WAIT_SECONDS = 5 * 60
TRANSPORT_ACTION = "transport_test"
RECIPIENT_REL = Path("install") / "recipient.json"
#: The transport test HTML is written to the code directory's runtime area so the
#: owner can open the exact file that is sent (never only an HTML text message).
TRANSPORT_HTML_REL = Path("install") / "transport-test.html"
TEST_HTML = (
    "<html><body>"
    "<h1>horizon 通道测试</h1>"
    "<p>这是一条测试 HTML：收到它说明控制台能用已记录的 Lark CLI 给你发消息。</p>"
    "<p>请点击随后到达的卡片上的按钮；控制台会核对点击者并写入 owner 记录。</p>"
    "</body></html>"
)

#: The ADR §7 order.  ``implemented`` flips in later tickets without reordering.
STEPS = (
    {
        "id": "archive",
        "title": "确认资料目录",
        "implemented": True,
        "ticket": "#84",
    },
    {
        "id": "agent",
        "title": "确认执行 Agent",
        "implemented": True,
        "ticket": "#84",
    },
    {
        "id": "lark",
        "title": "Lark CLI 检测与登录",
        "implemented": True,
        "ticket": "#85",
    },
    {
        "id": "transport",
        "title": "飞书测试消息与 owner 验证",
        "implemented": True,
        "ticket": "#86",
    },
    {
        "id": "naming",
        "title": "设施命名与自动创建",
        "implemented": True,
        "ticket": "#87",
    },
    {
        "id": "push_time",
        "title": "每日推送时刻",
        "implemented": True,
        "ticket": "#87",
    },
    {
        "id": "knowledge",
        "title": "知识库初始化选择",
        "implemented": True,
        "ticket": "#87",
    },
)
STEP_IDS = tuple(step["id"] for step in STEPS)

FULL_PERMISSION_HINT = "以完整权限重启"


# --------------------------------------------------------------------- paths
def state_path(home: Path) -> Path:
    """``<runtime>/install/initial/state.json``: the initialization state."""
    return Path(home) / STATE_REL


def _default_state() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "steps": {},
        "complete": False,
        "completed_at": None,
        "knowledge": "",
        "archive": "",
        "updated_at": None,
    }


def read_state(home: Path) -> dict:
    """The persisted initialization state; a missing file means "nothing done"."""
    stored = read_json(state_path(home)) or {}
    state = _default_state()
    if isinstance(stored, dict):
        state.update(stored)
    if not isinstance(state.get("steps"), dict):
        state["steps"] = {}
    previous = (state["steps"].get("agent") or {}).get("value") or {}
    if "dsh" in previous and previous.get("runtime") != "horizon-pi":
        state["steps"].pop("agent", None)
        state["complete"] = False
    return state


def record_step(home: Path, step: str, value: dict, *, note: str = "") -> dict:
    """Persist one completed step immediately; later steps read it back."""
    if step not in STEP_IDS:
        raise HostRunError(f"unknown initialization step: {step}")
    state = read_state(home)
    state["schema_version"] = SCHEMA_VERSION
    state["steps"][step] = {
        "status": "ok",
        "at": now(),
        "note": str(note or ""),
        "value": dict(value or {}),
    }
    state["updated_at"] = now()
    path = state_path(home)
    ensure_dir(path.parent)
    write_json(path, state)
    return state


def completed_steps(home: Path) -> list[str]:
    state = read_state(home)
    return [step for step in STEP_IDS if (state["steps"].get(step) or {}).get("status") == "ok"]


# ------------------------------------------------------------------- inputs
def _default_isatty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _say(out, text: str) -> None:
    try:
        out.write(str(text) + "\n")
        out.flush()
    except (OSError, ValueError, AttributeError):
        pass


def _ask(reader, out, prompt: str) -> str:
    """One prompt line; EOF/None/Ctrl+C aborts the whole flow."""
    _say(out, prompt)
    try:
        answer = reader(prompt)
    except (EOFError, StopIteration) as exc:
        raise _Aborted from exc
    if answer is None:
        raise _Aborted
    return str(answer).strip()


class _Aborted(Exception):
    """The user left the flow (EOF/Ctrl+C); completed steps stay persisted."""


def _code_dir(home: Path, resolved: dict | None) -> Path:
    if isinstance(resolved, dict) and resolved.get("code"):
        return Path(resolved["code"])
    return Path(home).parent


def _default_archive(home: Path, resolved: dict | None) -> Path:
    if isinstance(resolved, dict) and resolved.get("archive"):
        return Path(resolved["archive"])
    return layout.default_archive(_code_dir(home, resolved))


# -------------------------------------------------------------- write probe
def probe_write(directory: str | Path) -> dict:
    """A real create/delete cycle: placement alone never proves writability."""
    path = Path(directory).expanduser()
    try:
        if not path.is_dir():
            return {"ok": False, "path": str(path), "reason": "not_a_directory"}
        probe = path / f".horizon-init-probe-{uuid.uuid4().hex}"
        with probe.open("w", encoding="utf-8") as handle:
            handle.write("horizon init write probe\n")
        probe.unlink()
    except OSError as exc:
        return {
            "ok": False,
            "path": str(path),
            "reason": "unwritable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"ok": True, "path": str(path), "reason": ""}


def folder_dialog(default: str | Path) -> str | None:
    """The Windows system folder dialog; ``None`` means the user cancelled.

    Uses the stdlib ``tkinter`` picker (no extra dependency, no second terminal).
    A host without Tk cannot open it, so the caller falls back to the default
    directory rather than failing the step.
    """
    try:
        import tkinter
        from tkinter import filedialog
    except Exception:  # noqa: BLE001 - no Tk means no dialog, not a crash
        return None
    root = None
    try:
        root = tkinter.Tk()
        root.withdraw()
        chosen = filedialog.askdirectory(
            parent=root,
            title="选择 horizon 资料目录",
            initialdir=str(default),
            mustexist=True,
        )
        return str(chosen) if chosen else None
    except Exception:  # noqa: BLE001 - a closed/broken dialog cancels the choice
        return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:  # noqa: BLE001 - teardown must not raise
                pass


# ------------------------------------------------------------------- steps
def _step_archive(home, code: Path, default: Path, *, reader, out, pick_folder, write_probe) -> dict:
    _say(out, "步骤 1/7：确认资料目录（原文与推送件只放在这里）")
    _say(out, f"默认资料目录：{default}")
    while True:
        answer = _ask(
            reader,
            out,
            "使用默认资料目录？[Y] 确认（回车）/ [N] 选择其他文件夹：",
        )
        choice = answer.lower()
        if choice in ("", "y", "yes"):
            chosen, used_default = default, True
            break
        if choice in ("n", "no"):
            picked = pick_folder(str(default))
            if not picked:
                _say(out, "已取消文件夹选择，继续使用默认资料目录。")
                chosen, used_default = default, True
                break
            chosen = Path(picked).expanduser().resolve()
            used_default = False
            try:
                layout.resolve(code=str(code), archive=str(chosen))
            except HostRunError as exc:
                return {
                    "ok": False,
                    "reason": "archive_invalid",
                    "text": f"资料目录不可用：{exc}\n本次已停在“确认资料目录”，进度已保留。",
                }
            break
        _say(out, "请输入 Y 或 N。")

    outside = not _inside(chosen, code)
    if not used_default and outside:
        check = write_probe(chosen)
        if not check.get("ok"):
            launcher = console_launcher_path(code)
            detail = check.get("error") or check.get("reason") or "未知原因"
            return {
                "ok": False,
                "reason": "archive_unwritable",
                "text": (
                    f"资料目录不可写：{chosen}（{detail}）\n"
                    f"请双击 {launcher} {FULL_PERMISSION_HINT}并保留进度；"
                    "本次已停在“确认资料目录”，之后可继续 /horizon-init。"
                ),
                "path": str(chosen),
            }

    record_step(
        home,
        "archive",
        {
            "path": str(chosen),
            "default": used_default,
            "outside_code": outside,
        },
    )
    _say(out, f"[完成] 资料目录：{chosen}")
    return {"ok": True, "path": str(chosen), "default": used_default}


def agent_facts(home: Path, *, code: Path, archive: Path) -> dict:
    from hostrun import embedded_agent
    facts = embedded_agent.runtime(home, code=code)
    return {**facts, "usable": facts["ok"], "cwd": str(code), "archive": str(archive),
            "runtime": "horizon-pi"}


def _step_agent(home: Path, *, code: Path, archive: Path, reader, out) -> dict:
    from hostrun import embedded_agent
    import getpass
    facts = agent_facts(home, code=code, archive=archive)
    _say(out, "步骤 2/7：配置 Horizon 内置 Agent 的模型服务")
    if not facts["usable"]:
        _say(out, "内置 Agent 不可用：" + "；".join(facts["problems"]))
        return {"ok": False, "reason": "agent_unavailable", "text": "；".join(facts["problems"]), "facts": facts}
    if not facts["configured"]:
        provider = _ask(reader, out, "模型服务标识（例如 deepseek / anthropic / openai）：").strip()
        model = _ask(reader, out, "模型名称：").strip()
        base_url = _ask(reader, out, "OpenAI 兼容接口地址（使用内置服务时留空）：").strip()
        _say(out, "API Key 仅保存在本机 .horizon/agent/model.json，请保护此文件；不会写入运行日志。")
        key = getpass.getpass("模型 API Key（隐藏输入）：") if sys.stdin is not None and sys.stdin.isatty() else _ask(reader, out, "模型 API Key：")
        try:
            embedded_agent.configure(home, provider=provider, model=model, api_key=key, base_url=base_url)
        except HostRunError as exc:
            return {"ok": False, "reason": "model_config_invalid", "text": str(exc)}
        facts = agent_facts(home, code=code, archive=archive)
    _say(out, f"内置 Pi Agent：{facts['provider']} / {facts['model']}")
    answer = _ask(reader, out, "确认使用此模型配置？[Y] 确认（回车）/ [N] 取消：")
    if answer.lower() not in ("", "y", "yes"):
        return {"ok": False, "reason": "declined", "text": "已取消模型配置确认；完成的步骤已保留。", "facts": facts}
    record_step(home, "agent", facts)
    _say(out, "[完成] 内置 Agent 模型已确认。")
    return {"ok": True, "facts": facts}


# -------------------------------------------------------------- Lark CLI step
def lark_cli_facts(home: Path, *, code: Path, runtime: Path) -> dict:
    """Resolve the CLI without installing or logging in (ADR 0007 first).

    A recorded absolute path wins and is reused.  A recorded path that no longer
    exists is *stale*: this function also looks for another installed CLI, but
    the step must ask before adopting it — never a silent downgrade.
    """
    config = load_config(Path(home))
    recorded = str((config.get("paths") or {}).get("lark_cli") or "").strip()
    found = lark.discover_cli(code, spec=recorded or None, home=runtime)
    if found.get("ok"):
        source = "recorded" if (recorded and found.get("source") == "explicit") else "installed"
        return {"status": "found", "source": source, "path": found["path"], "recorded": recorded}
    if found.get("stale"):
        other = lark.discover_cli(code, spec=None, home=runtime)
        return {
            "status": "stale",
            "recorded": recorded,
            "note": found.get("note") or "",
            "other": {"path": other["path"], "source": other.get("source") or ""}
            if other.get("ok")
            else None,
        }
    return {"status": "missing", "recorded": recorded, "note": found.get("note") or ""}


def _confirm_cli_adoption(found: dict, *, recorded: str, reader, out) -> bool:
    """Ask before reusing a CLI that is not the recorded one (ADR 0007)."""
    if recorded:
        _say(out, f"安装记录中的飞书 CLI 已失效：{recorded}（不会自动降级到其他版本）")
    _say(out, f"检测到已安装的飞书 CLI：{found['path']}（来源 {found.get('source') or '未记录'}）")
    answer = _ask(
        reader,
        out,
        "采用该 CLI 并写入安装记录？[Y] 采用（回车）/ [N] 私有安装一份新的：",
    )
    return answer.lower() in ("", "y", "yes")


def _failure_choice(reader, out, *, what: str) -> str:
    """The ADR §7 failure menu: retry or abort, never skip."""
    while True:
        answer = _ask(
            reader,
            out,
            f"{what}未完成：[R] 重试 / [A] 中止（进度保留，不允许跳过本步骤）：",
        )
        choice = answer.lower()
        if choice in ("r", "retry"):
            return "retry"
        if choice in ("a", "abort"):
            return "abort"
        _say(out, "请输入 R 重试或 A 中止；本步骤不能跳过。")


def _lark_abort(what: str) -> dict:
    return {
        "ok": False,
        "reason": "lark_aborted",
        "text": (
            f"{what}未完成，已中止；进度已保留（已完成的初始化步骤已保存）。"
            "下次 /horizon-init 会从 Lark CLI 步骤继续（不允许跳过）。"
        ),
    }


def _lark_aim(code: Path) -> Path:
    return Path(code) / RUNTIME_DIRNAME


def _install_private_cli(home: Path, *, code: Path, reader, out, runner, install) -> dict:
    """Private ``@larksuite/cli`` under the code-directory runtime area."""
    while True:
        _say(out, f"正在私有安装 @larksuite/cli 到代码目录运行时区：{_lark_aim(code)}")
        result = install(code, runner=runner)
        path = str(result.get("path") or "")
        if result.get("ok") and path and Path(path).is_file():
            update_config(home, paths={"lark_cli": path})
            _say(out, f"[完成] 已私有安装飞书 CLI：{path}（不装全局包，不写 PATH 或注册表）")
            return {
                "ok": True,
                "path": path,
                "source": "private-install",
                "version": result.get("version") or "",
            }
        note = result.get("note") or result.get("reason") or "未知原因"
        _say(out, f"私有安装失败：{note}")
        if _failure_choice(reader, out, what="私有安装") == "abort":
            return _lark_abort("Lark CLI 私有安装")


def _ensure_lark_cli(home: Path, *, code: Path, reader, out, runner, install) -> dict:
    facts = lark_cli_facts(home, code=code, runtime=Path(home))
    if facts["status"] == "found" and facts["source"] == "recorded":
        _say(out, f"[复用] 已记录的飞书 CLI：{facts['path']}（ADR 0007：不重复安装）")
        return {"ok": True, "path": facts["path"], "source": "recorded"}
    if facts["status"] == "found":
        if _confirm_cli_adoption(facts, recorded="", reader=reader, out=out):
            update_config(home, paths={"lark_cli": facts["path"]})
            _say(out, f"[完成] 已记录飞书 CLI：{facts['path']}")
            return {"ok": True, "path": facts["path"], "source": "adopted"}
        return _install_private_cli(home, code=code, reader=reader, out=out, runner=runner, install=install)
    if facts["status"] == "stale":
        _say(out, facts.get("note") or "记录的飞书 CLI 路径已失效。")
        other = facts.get("other")
        if other and _confirm_cli_adoption(
            other, recorded=facts.get("recorded") or "", reader=reader, out=out
        ):
            update_config(home, paths={"lark_cli": other["path"]})
            _say(out, f"[完成] 已记录飞书 CLI：{other['path']}")
            return {"ok": True, "path": other["path"], "source": "adopted"}
        if not other:
            _say(out, "没有找到其他可用的飞书 CLI；将私有安装一份新的。")
    else:
        _say(out, f"未找到可用的飞书 CLI：{facts.get('note') or '未安装'}")
    return _install_private_cli(home, code=code, reader=reader, out=out, runner=runner, install=install)


def _identity_facts(cli: str, *, runner) -> dict:
    """Booleans only: never a token, an open id or a raw credential payload."""
    result = lark.identity(cli, runner=runner)
    evidence = result.get("evidence") or {}
    ready = [str(item) for item in (evidence.get("ready") or [])]
    return {
        "status": result.get("status") or "blocked",
        "note": result.get("note") or "",
        "user_ready": "user" in ready,
        "bot_ready": "bot" in ready,
        "missing": [str(item) for item in (evidence.get("missing") or [])],
        "expired": bool(evidence.get("expired")),
        "refresh_pending": [str(item) for item in (evidence.get("refresh_pending") or [])],
        "dpop_key_status": str(evidence.get("dpop_key_status") or ""),
        "dpop_mode": str(evidence.get("dpop_mode") or ""),
        "scopes": list(result.get("scopes") or []),
    }


def _authorization(scopes: list[str]) -> dict:
    return lark.authorization(list(scopes or []), required=list(LARK_REQUIRED_SCOPES))


def _device_login(*, cli: str, reader, out, runner, clock, sleep) -> dict:
    """Split device-code login: show the link + code, poll, continue by itself."""
    attempt = 0
    while True:
        attempt += 1
        request = lark.request_login(cli, runner=runner)
        if not request.get("ok"):
            _say(out, request.get("note") or "无法取得登录链接与设备码。")
            if _failure_choice(reader, out, what="Lark 登录") == "abort":
                return _lark_abort("Lark 登录")
            continue
        _say(out, f"授权链接：{request['verification_url']}")
        _say(out, f"设备码：{request['device_code']}")
        _say(
            out,
            "请在浏览器打开上面的链接完成授权；控制台会自动继续"
            f"（等待上限 {LARK_WAIT_SECONDS // 60} 分钟，不新开终端）。",
        )
        deadline = clock() + LARK_WAIT_SECONDS
        polls = 0
        last_report = clock()
        while clock() < deadline:
            polls += 1
            remaining = max(1, int(deadline - clock()))
            result = lark.complete_login(
                cli,
                request["device_code"],
                runner=runner,
                timeout=min(LARK_COMPLETE_TIMEOUT, remaining),
            )
            if result.get("ok"):
                identity = _identity_facts(cli, runner=runner)
                if identity["user_ready"]:
                    _say(out, "[完成] 飞书 user 身份已登录。")
                    return {
                        "ok": True,
                        "identity": identity,
                        "login": {"device": True, "polls": polls, "attempts": attempt},
                    }
            moment = clock()
            if moment - last_report >= 60:
                _say(
                    out,
                    "等待授权中…剩余约 "
                    f"{max(0, int(deadline - moment)) // 60} 分钟（设备码登录，未跳过）。",
                )
                last_report = moment
            remaining = deadline - clock()
            if remaining <= 0:
                break
            sleep(min(LARK_POLL_SECONDS, remaining))
        _say(out, f"登录等待已超过 {LARK_WAIT_SECONDS // 60} 分钟，尚未完成授权。")
        if _failure_choice(reader, out, what="Lark 登录") == "abort":
            return _lark_abort("Lark 登录")


def _permission_guidance(
    *, cli: str, identity: dict, authorization: dict, reader, out, runner, clock
) -> dict:
    """Scope/bot gap: print the developer-console link and wait for a re-check."""
    deadline = clock() + LARK_WAIT_SECONDS
    while True:
        _say(out, f"飞书开发者后台：{lark.DEVELOPER_CONSOLE_URL}")
        missing = list(authorization.get("missing") or [])
        if missing:
            _say(out, "缺失授权范围：" + "、".join(missing))
        elif authorization.get("status") == "untested":
            _say(
                out,
                authorization.get("note")
                or "登录状态未报告授权范围；权限由后续测试消息步骤验证。",
            )
        if not identity["bot_ready"]:
            _say(out, "bot 身份未就绪：bot scope 只能在飞书开发者后台开通，auth login 不用于 bot。")
        if clock() >= deadline:
            _say(out, f"权限复检等待已超过 {LARK_WAIT_SECONDS // 60} 分钟。")
            if _failure_choice(reader, out, what="权限复检") == "abort":
                return _lark_abort("飞书权限复检")
            deadline = clock() + LARK_WAIT_SECONDS
            continue
        answer = _ask(reader, out, "在开发者后台完成配置后按回车重新检查 / [A] 中止（进度保留）：")
        if answer.lower() in ("a", "abort"):
            return _lark_abort("飞书权限复检")
        identity = _identity_facts(cli, runner=runner)
        authorization = _authorization(identity["scopes"])
        if (
            identity["user_ready"]
            and identity["bot_ready"]
            and authorization.get("status") != "blocked"
        ):
            _say(out, "[完成] 飞书授权范围已齐全。")
            return {"ok": True, "identity": identity, "authorization": authorization}


def _step_lark(
    home: Path,
    *,
    code: Path,
    reader,
    out,
    runner,
    install,
    clock,
    sleep,
) -> dict:
    _say(out, "步骤 3/7：Lark CLI 三级检测（复用已配置 / 采用已安装 / 私有安装）")
    ensured = _ensure_lark_cli(
        home, code=code, reader=reader, out=out, runner=runner, install=install
    )
    if not ensured.get("ok"):
        return ensured
    cli = ensured["path"]

    identity = _identity_facts(cli, runner=runner)
    login = {"device": False, "polls": 0, "attempts": 0}
    if not identity["user_ready"]:
        _say(out, "飞书身份未登录；开始设备码登录（在控制台内完成）。")
        outcome = _device_login(cli=cli, reader=reader, out=out, runner=runner, clock=clock, sleep=sleep)
        if not outcome.get("ok"):
            return outcome
        identity = outcome["identity"]
        login = outcome["login"]
    authorization = _authorization(identity["scopes"])
    if authorization.get("status") == "blocked" or not identity["bot_ready"]:
        _say(out, "飞书权限或 scope 不足；等待你在开发者后台补齐后复检。")
        outcome = _permission_guidance(
            cli=cli,
            identity=identity,
            authorization=authorization,
            reader=reader,
            out=out,
            runner=runner,
            clock=clock,
        )
        if not outcome.get("ok"):
            return outcome
        identity = outcome["identity"]
        authorization = outcome["authorization"]

    value = {
        "cli": {"path": cli, "source": ensured["source"]},
        "identity": {
            "user_ready": identity["user_ready"],
            "bot_ready": identity["bot_ready"],
            "missing": identity["missing"],
            "expired": identity["expired"],
            "refresh_pending": identity["refresh_pending"],
            "dpop_key_status": identity["dpop_key_status"],
            "dpop_mode": identity["dpop_mode"],
        },
        "authorization": {
            "status": authorization.get("status") or "",
            "missing": list(authorization.get("missing") or []),
            "required_count": len(authorization.get("required") or []),
            "granted_count": len(authorization.get("granted") or []),
            "note": authorization.get("note") or "",
        },
        "login": login,
    }
    note = "飞书 CLI 已复用，身份与授权范围已检查"
    if ensured["source"] == "private-install":
        note = "飞书 CLI 已私有安装到代码目录运行时区，身份与授权范围已检查"
    elif ensured["source"] == "adopted":
        note = "已采用并记录已有飞书 CLI，身份与授权范围已检查"
    record_step(home, "lark", value, note=note)
    _say(out, f"[完成] Lark CLI：{cli}")
    return {"ok": True, "value": value}


# ------------------------------------------------------- transport test step
def transport_test_card() -> dict:
    """One Card 2.0 payload with exactly one button (reusing ``cards._button``)."""
    from publisher.cards import _button

    return {
        "schema": "2.0",
        "config": {
            "update_multi": True,
            "summary": {"content": "horizon 通道测试"},
        },
        "header": {
            "title": {"tag": "plain_text", "content": "horizon 通道测试"},
            "template": "blue",
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 20px 12px",
            "vertical_spacing": "12px",
            "elements": [
                {
                    "tag": "markdown",
                    "content": "点击下面的按钮，证明飞书通道与 owner 核实都可用。",
                },
                {
                    "tag": "form",
                    "name": "transport_form",
                    "vertical_spacing": "12px",
                    "elements": [
                        _button(
                            TRANSPORT_ACTION,
                            "我已收到，确认通道可用",
                            kind="primary",
                        )
                    ],
                },
            ],
        },
    }


def send_transport_test(
    *,
    owner: str,
    html: str,
    card: dict,
    html_file: str | None = None,
    identity: str = "bot",
) -> dict:
    """Send the test HTML file (or text fallback) and the single-button card."""
    from publisher import feishu

    file_path = Path(html_file) if html_file else None
    try:
        if file_path is not None:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(html, encoding="utf-8")
            html_message = feishu.send_file(owner, str(file_path), identity=identity)
        else:  # durable fallback for hosts that cannot send files
            html_message = feishu.send_text(owner, html, identity=identity)
        card_message = feishu.send_card(owner, card, identity=identity)
    except Exception as exc:  # noqa: BLE001 - a send failure is a retryable result
        return {
            "ok": False,
            "reason": "transport_send_failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    card_message_id = str(card_message.get("message_id") or "")
    if not card_message_id:
        return {
            "ok": False,
            "reason": "transport_send_failed",
            "error": "卡片发送成功但没有 message_id",
        }
    return {
        "ok": True,
        "html_message_id": str(html_message.get("message_id") or ""),
        "html_file": str(file_path) if file_path is not None else "",
        "card_message_id": card_message_id,
    }


def wait_card_callback(
    *,
    timeout_seconds: int = TRANSPORT_WAIT_SECONDS,
    identity: str = "bot",
    consume=None,
) -> dict:
    """Wait for one ``card.action.trigger`` event through the injectable source.

    ``consume`` is the lark event source (default
    :func:`publisher.feishu.listen_events`); it is injected in tests so no real
    ``lark-cli`` process is ever spawned.  Only the first event stops the chunk.
    """
    from hostrun.console_events import parse_card_event
    from publisher import feishu

    consume = consume or feishu.listen_events
    lines: list[str] = []

    def on_line(line: str):
        lines.append(str(line))
        return False

    try:
        chunk = consume(
            identity=identity,
            event_key="card.action.trigger",
            max_events=1,
            timeout=f"{int(timeout_seconds)}s",
            on_line=on_line,
            deadline_seconds=timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - an event-source failure is a result
        return {
            "ok": False,
            "reason": "consume_failed",
            "events": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
    events = []
    for line in lines:
        try:
            raw = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        event = parse_card_event(raw)
        if event:
            events.append(event)
    if events:
        return {"ok": True, "reason": "ok", "events": events, "chunk": chunk}
    if chunk.get("reason") in ("timeout", "stopped") or chunk.get("ok"):
        return {"ok": False, "reason": "timeout", "events": [], "chunk": chunk}
    return {
        "ok": False,
        "reason": "consume_failed",
        "events": [],
        "chunk": chunk,
        "error": chunk.get("stderr_tail") or chunk.get("reason") or "",
    }


def _transport_failure(reason: str, text: str) -> dict:
    return {"ok": False, "reason": reason, "text": text}


def _step_transport(
    home: Path,
    *,
    reader,
    out,
    runner,
    send,
    consume,
) -> dict:
    """Send the test HTML + one-button card, wait for the owner's click, write owner."""
    _say(out, "步骤 4/7：飞书测试消息与 owner 验证")
    config = load_config(Path(home))
    cli = str((config.get("paths") or {}).get("lark_cli") or "").strip()
    if not cli:
        return _transport_failure(
            "recipient_unverified",
            "安装记录没有可用的飞书 CLI，无法发送测试消息；"
            "请先完成 Lark CLI 步骤，再运行 /horizon-init。",
        )
    wait_minutes = TRANSPORT_WAIT_SECONDS // 60
    while True:
        # Re-check the authenticated user before every send: a retried test must
        # never target a stale or different identity (ADR 0007 / #53).
        identity = lark.user_open_id(cli, runner=runner)
        if not identity.get("ok"):
            _say(out, "无法核对当前登录的飞书用户身份（未登录、令牌不可用或 CLI 失效）。")
            if _failure_choice(reader, out, what="飞书通道测试") == "abort":
                return _transport_failure(
                    "recipient_unverified",
                    "未核实 owner：无法读取当前登录的飞书用户身份；"
                    "初始化停在“飞书测试消息与 owner 验证”，进度已保留。",
                )
            continue
        owner = str(identity["open_id"])
        html_path = Path(home) / TRANSPORT_HTML_REL
        try:
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_text(TEST_HTML, encoding="utf-8")
        except OSError as exc:
            return _transport_failure(
                "transport_html_write_failed",
                f"无法把测试 HTML 写入代码目录（{html_path}）：{exc}",
            )
        _say(out, f"正在发送测试 HTML 文件（{html_path}）与单按钮测试卡片…")
        sent = send(
            owner=owner,
            html=TEST_HTML,
            card=transport_test_card(),
            html_file=str(html_path),
        )
        if not sent.get("ok"):
            detail = sent.get("error") or sent.get("reason") or "未知原因"
            _say(out, f"测试消息发送失败：{detail}")
            if _failure_choice(reader, out, what="飞书测试消息发送") == "abort":
                return _transport_failure(
                    "transport_send_failed",
                    f"测试消息发送失败（{detail}）；"
                    "初始化停在“飞书测试消息与 owner 验证”，进度已保留。",
                )
            continue
        card_message_id = str(sent.get("card_message_id") or "")
        _say(
            out,
            f"测试消息已发送；请在飞书点击测试卡片上的按钮"
            f"（等待上限 {wait_minutes} 分钟，不新开终端）。",
        )
        waited = consume(timeout_seconds=TRANSPORT_WAIT_SECONDS, identity="bot")
        if not waited.get("ok"):
            if waited.get("reason") == "consume_failed":
                _say(out, "等待卡片回调失败：卡片事件源不可用。")
                if _failure_choice(reader, out, what="飞书卡片回调") == "abort":
                    return _transport_failure(
                        "transport_event_failed",
                        "无法接收卡片回调；初始化停在“飞书测试消息与 owner 验证”，进度已保留。",
                    )
            else:
                _say(out, f"未在 {wait_minutes} 分钟内收到测试卡片的按钮点击。")
                if _failure_choice(reader, out, what="飞书测试消息点击") == "abort":
                    return _transport_failure(
                        "recipient_unverified",
                        "未收到测试卡片点击，owner 未核实；"
                        "初始化停在“飞书测试消息与 owner 验证”，可重试重发。",
                    )
            continue

        mismatch = None
        matched = None
        for event in waited.get("events") or []:
            message_id = str(event.get("message_id") or "")
            # Ignore callbacks for other cards.  A missing id is tolerated
            # because the injected source already targets one event key.
            if card_message_id and message_id and message_id != card_message_id:
                continue
            action = str(event.get("action_name") or "")
            if action and action != TRANSPORT_ACTION:
                continue
            if str(event.get("operator_id") or "") != owner:
                mismatch = event
                continue
            matched = event
            break
        if matched:
            write_json(
                home / RECIPIENT_REL,
                {
                    "schema_version": 1,
                    "owner": owner,
                    "verified_at": now(),
                    "source": "init.transport",
                },
            )
            value = {
                "channel": "ok",
                "owner": {
                    "verified": True,
                    "record": str(RECIPIENT_REL),
                    "source": "init.transport",
                },
                "html_message_id": str(sent.get("html_message_id") or ""),
                "html_file": str(sent.get("html_file") or html_path),
                "card_message_id": card_message_id,
            }
            record_step(home, "transport", value, note="飞书通道测试通过，owner 已核实")
            # The open id original is never echoed back to the terminal.
            _say(out, "[完成] 飞书通道测试通过；owner 已核实并写入 recipient.json。")
            return {"ok": True, "value": value}
        if mismatch is not None:
            _say(out, "卡片被其他人点击：点击者与当前登录用户不一致，未写入 owner。")
            if _failure_choice(reader, out, what="飞书 owner 核实") == "abort":
                return _transport_failure(
                    "recipient_mismatch",
                    "点击者与当前登录用户不一致，owner 未核实；"
                    "初始化停在“飞书测试消息与 owner 验证”，可重试重发。",
                )
            continue
        _say(out, "等待期间没有收到本卡片的有效点击。")
        if _failure_choice(reader, out, what="飞书测试消息点击") == "abort":
            return _transport_failure(
                "recipient_unverified",
                "未收到测试卡片点击，owner 未核实；"
                "初始化停在“飞书测试消息与 owner 验证”，可重试重发。",
            )


# ------------------------------------------------- naming / facility creation
#: The three names the user may see and change.  Table names are fixed by the
#: knowledge contract and are deliberately never shown or asked (#87 / ADR §7).
NAMING_PROMPTS = (
    ("base", "Base 名称", None),
    ("source", "原文云盘目录名", "source"),
    ("push", "推送云盘目录名", "push"),
)
INVALID_NAME_TEXT = "名称不可用"


def default_find_same_name(home: Path, name: str, cli_bin: str) -> list[dict]:
    """Look up an existing Base by display name; a failed lookup is not a blocker."""
    try:
        from library.admin import LarkAdminClient

        resolved = LarkAdminClient(bin=cli_bin).resolve_title(name)
    except Exception:  # noqa: BLE001 - a lookup failure must not block creation
        return []
    return [dict(item) for item in ((resolved or {}).get("matches") or []) if item]


def _naming_overrides(chosen: dict) -> dict:
    """Empty answers mean "use the default", so they are left out of the overrides."""
    overrides: dict = {}
    if chosen.get("base"):
        overrides["base"] = chosen["base"]
    folders = {
        key: chosen[key] for key in ("source", "push") if chosen.get(key)
    }
    if folders:
        overrides["folders"] = folders
    return overrides


def _ask_name(reader, out, label: str, default: str, chosen: dict, key: str) -> str:
    """One name: Enter keeps the default, text overrides; invalid input is re-asked."""
    while True:
        answer = _ask(reader, out, f"{label}（回车默认：{default}）：")
        trial = {**chosen, key: answer}
        try:
            facilities_mod.normalize(_naming_overrides(trial))
        except HostRunError as exc:
            _say(out, f"{INVALID_NAME_TEXT}：{exc}；请重新输入{label}。")
            continue
        return answer


def _choose_existing(matches: list[dict], *, reader, out) -> bool:
    """Ask reuse-or-create for a same-name Base; Enter means "create"."""
    labels = "、".join(str(item.get("name") or "") for item in matches) or "同名 Base"
    _say(out, f"已存在 {len(matches)} 个同名 Base：{labels}。")
    while True:
        answer = _ask(
            reader,
            out,
            "复用已有的 Base 还是新建一个？[R] 复用（不重复创建）/ [N] 新建（回车）：",
        )
        choice = answer.lower()
        if choice in ("", "n", "new"):
            return False
        if choice in ("r", "reuse"):
            return True
        _say(out, "请输入 R 复用或 N 新建。")


def _naming_value(outcome: dict, names: dict, *, reused: bool) -> dict:
    tables = {
        key: str((entry or {}).get("id") or "")
        for key, entry in (outcome.get("tables") or {}).items()
    }
    base = outcome.get("base") or {}
    return {
        "names": outcome.get("names") or names,
        "base": {
            "token": str(base.get("token") or ""),
            "url": str(base.get("url") or ""),
            "name": str(base.get("name") or names.get("base") or ""),
        },
        "tables": tables,
        "folders": dict(outcome.get("folders") or {}),
        "reused": bool(reused),
        "actions": [str(item) for item in (outcome.get("actions") or [])],
    }


def _step_naming(
    home: Path,
    *,
    code: Path,
    reader,
    out,
    find_same_name,
    facility_create,
    facility_attach,
) -> dict:
    """Ask the three names, then create (or reuse) every facility automatically."""
    _say(out, "步骤 5/7：设施命名（表名固定，不询问；回车用默认，输入覆盖）")
    recorded = facilities_mod.load_confirmed(home) or {}
    defaults = facilities_mod.normalize(recorded or {})
    chosen: dict = {}
    for key, label, folder_key in NAMING_PROMPTS:
        default = (
            defaults["folders"][folder_key] if folder_key else defaults["base"]
        )
        chosen[key] = _ask_name(reader, out, label, default, chosen, key)
    names = facilities_mod.normalize(_naming_overrides(chosen))
    _say(
        out,
        "将创建：Base " + names["base"]
        + "；原文目录 " + names["folders"]["source"]
        + "；推送目录 " + names["folders"]["push"]
        + "；表名固定不展示。",
    )

    config = load_config(Path(home))
    cli_bin = str((config.get("paths") or {}).get("lark_cli") or "").strip() or "lark-cli"
    matches = list((find_same_name or default_find_same_name)(home, names["base"], cli_bin) or [])
    reuse = bool(matches) and _choose_existing(matches, reader=reader, out=out)

    try:
        if reuse:
            match = matches[0]
            link = str(match.get("url") or match.get("base_token") or "")
            _say(out, f"复用已有 Base：{match.get('name') or names['base']}（只补缺失的表与目录）")
            outcome = facility_attach(
                home,
                link,
                names=names,
                confirmed=True,
                create_missing=True,
                cli_bin=cli_bin,
            )
        else:
            outcome = facility_create(
                home, names=names, confirmed=True, cli_bin=cli_bin
            )
    except (HostRunError, OSError) as exc:
        return {
            "ok": False,
            "reason": "facilities_failed",
            "text": f"设施创建失败：{exc}；初始化停在“设施命名与自动创建”，进度已保留。",
        }
    if not outcome.get("ok"):
        detail = outcome.get("note") or outcome.get("reason") or "未知原因"
        return {
            "ok": False,
            "reason": "facilities_failed",
            "text": (
                f"设施未就绪：{detail}；初始化停在“设施命名与自动创建”，"
                "已创建的部分会被记录并复用，重跑不会重复创建。"
            ),
            "outcome": outcome,
        }
    value = _naming_value(outcome, names, reused=bool(reuse or outcome.get("reused")))
    record_step(
        home,
        "naming",
        value,
        note=("复用已有 Base 并补齐设施" if value["reused"] else "已自动创建设施并记录稳定 id"),
    )
    _say(
        out,
        f"[完成] 设施已就绪：{value['base']['name']}"
        f"（{len(value['tables'])} 张表，目录 {len(value['folders'])} 个）",
    )
    return {"ok": True, "value": value}


# --------------------------------------------------------------- push time
def _step_push_time(home: Path, *, reader, out, set_push_time=None) -> dict:
    """One daily slot in the user's local timezone, validated and persisted."""
    setter = set_push_time or console_service.set_push_time
    default = console_service.DEFAULT_PUSH_TIME
    _say(out, f"步骤 6/7：每日推送时刻（用户本地时区，单一时段，默认 {default}）")
    while True:
        answer = _ask(reader, out, f"每日推送时刻 HH:MM（回车默认 {default}）：")
        text = answer or default
        result = setter(home, text)
        if result.get("ok"):
            value = {
                "push_time": str(result.get("push_time") or text),
                "next_push_at": result.get("next_push_at"),
            }
            record_step(home, "push_time", value, note="每日推送时刻已保存")
            _say(
                out,
                f"[完成] 每日推送时刻：{value['push_time']}"
                f"（下一次：{value['next_push_at'] or '服务暂停时不排期'}）",
            )
            return {"ok": True, "value": value}
        _say(out, f"时刻不合法：{text}（要求 HH:MM，00:00–23:59）。请重新输入。")
        if answer == "":
            # Defensive: an invalid *default* must not loop forever.
            return {
                "ok": False,
                "reason": "invalid_push_time",
                "text": f"每日推送时刻不合法：{text}；初始化停在“每日推送时刻”，进度已保留。",
            }


# ----------------------------------------------------------- knowledge gate
def _step_knowledge(home: Path, *, reader, out) -> dict:
    """Ask whether to initialize the knowledge base now; record the gate decision."""
    _say(out, "步骤 7/7：知识库初始化")
    _say(
        out,
        "知识库是每日推送的来源；暂不初始化时服务照常运行，但每日推送会被知识库门禁挡住。",
    )
    while True:
        answer = _ask(
            reader,
            out,
            "现在初始化知识库（进入 /horizon-update-kb）？[Y] 是 / [N] 暂不初始化（回车）：",
        )
        choice = answer.lower()
        if choice in ("", "n", "no", "2"):
            value = {"choice": "later", "gate": "blocked", "reason": console_gate.REASON}
            record_step(
                home, "knowledge", value, note="暂不初始化知识库：每日推送被门禁挡住"
            )
            console_gate.set_knowledge_ready(home, False, source="init.later")
            _say(out, "[完成] 已选择暂不初始化知识库；每日推送将被门禁挡住。")
            return {"ok": True, "choice": "later", "value": value}
        if choice in ("y", "yes", "1"):
            value = {"choice": "update_kb", "gate": "pending", "reason": console_gate.REASON}
            record_step(
                home, "knowledge", value, note="进入 /horizon-update-kb 初始化知识库"
            )
            # Closed until #90's completed review releases it: a pending
            # initialization must not let the daily push slip through.
            console_gate.set_knowledge_ready(home, False, source="init.update_kb")
            _say(out, "[完成] 将进入 /horizon-update-kb 初始化知识库。")
            return {"ok": True, "choice": "update_kb", "value": value}
        _say(out, "请输入 Y（初始化）或 N（暂不初始化）。")


# ------------------------------------------------------------- completion
def _apply_archive(
    home: Path, code: Path, archive_value: dict, *, probe=probe_write
) -> dict:
    """Apply the archive confirmed in step 1: resolve, re-probe, create, remember."""
    path = str((archive_value or {}).get("path") or "").strip()
    if not path:
        return {"ok": True, "path": "", "note": "未记录资料目录，沿用当前解析结果"}
    try:
        resolved = layout.resolve(code=str(code), archive=path)
        # Step 1 probed a write once; the directory can become unwritable before
        # finalization, so re-check before the layout is persisted and the first
        # push depends on it (S5).
        if (archive_value or {}).get("outside_code") and not (archive_value or {}).get(
            "default"
        ):
            check = probe(resolved["archive"])
            if not check.get("ok"):
                detail = check.get("error") or check.get("reason") or "未知原因"
                return {
                    "ok": False,
                    "reason": "archive_unwritable",
                    "path": path,
                    "note": (
                        f"资料目录已不可写：{resolved['archive']}（{detail}）；"
                        "初始化未完成，可修正权限后重跑 /horizon-init。"
                    ),
                }
        layout.ensure(resolved)
        layout.remember(resolved)
    except (HostRunError, OSError) as exc:
        return {
            "ok": False,
            "reason": "archive_apply_failed",
            "path": path,
            "note": f"资料目录未能生效：{exc}",
        }
    return {"ok": True, "path": resolved["archive"], "note": "资料目录已生效"}


def mark_complete(home: Path, *, knowledge: str, archive: str) -> dict:
    """Write the completion state the console and later tickets read back."""
    state = read_state(home)
    state["schema_version"] = SCHEMA_VERSION
    state["complete"] = True
    state["completed_at"] = now()
    state["knowledge"] = str(knowledge or "")
    state["archive"] = str(archive or "")
    state["updated_at"] = now()
    path = state_path(home)
    ensure_dir(path.parent)
    write_json(path, state)
    return state


def is_complete(home: Path) -> bool:
    state = read_state(home)
    if not state.get("complete"):
        return False
    return all(
        (state["steps"].get(step) or {}).get("status") == "ok"
        for step in STEP_IDS
    )


def _finalize(
    home: Path,
    *,
    code: Path,
    state: dict,
    service,
    update_kb,
    out,
    probe=probe_write,
) -> dict:
    """Apply the archive, start the service, mark completion, then hand off to KB."""
    archive_value = ((state["steps"].get("archive") or {}).get("value")) or {}
    applied = _apply_archive(home, code, archive_value, probe=probe)
    if not applied.get("ok"):
        return {
            "ok": False,
            "reason": applied.get("reason") or "archive_apply_failed",
            "text": f"{applied.get('note')}；初始化未完成，可修正后重跑 /horizon-init。",
            "archive": applied,
        }
    try:
        started = dict(service.start() or {})
    except Exception as exc:  # noqa: BLE001 - a failed start is reported, not raised
        started = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not started.get("ok", True):
        return {
            "ok": False,
            "reason": "service_start_failed",
            "text": (
                "初始化步骤已完成，但服务未能自动启动："
                f"{started.get('reason') or '未知原因'}；请执行 /horizon-start。"
            ),
            "archive": applied,
            "service": started,
        }
    choice = str(((state["steps"].get("knowledge") or {}).get("value") or {}).get("choice") or "later")
    complete = mark_complete(home, knowledge=choice, archive=applied.get("path") or "")
    lines = [f"初始化已全部完成（{complete['completed_at']}）；服务已启动。"]
    handoff = None
    if choice == "update_kb":
        if update_kb is None:
            lines.append("下一步执行 /horizon-update-kb 初始化知识库（完成后自动放行推送）。")
        else:
            try:
                handoff = update_kb()
            except Exception as exc:  # noqa: BLE001 - a failed handoff is reported
                handoff = {
                    "ok": False,
                    "reason": "update_kb_failed",
                    "text": f"进入 /horizon-update-kb 失败：{type(exc).__name__}: {exc}",
                }
            handoff = handoff if isinstance(handoff, dict) else {"ok": False}
            if handoff.get("text"):
                lines.append(str(handoff["text"]))
    gate = console_gate.knowledge_gate(home)
    if not gate.get("ready"):
        lines.append(gate["note"])
    elif choice == "later":
        lines.append(gate["note"])
    lines.append(
        f"知识库门禁：{'已就绪' if gate.get('ready') else '待初始化知识库'}"
        f"；每日推送时刻：{console_service.configured_push_time(home)}"
    )
    return {
        "ok": True,
        "reason": "update_kb" if choice == "update_kb" else "knowledge_pending",
        "text": "\n".join(lines),
        "archive": applied,
        "service": started,
        "handoff": handoff,
        "knowledge_gate": gate,
        "state": {
            "complete": complete.get("complete"),
            "completed_at": complete.get("completed_at"),
            "knowledge": complete.get("knowledge"),
            "archive": complete.get("archive"),
        },
    }


# --------------------------------------------------------------------- run
def console_launcher_path(code: Path) -> Path:
    return Path(code) / "bin" / "horizon-console.cmd"


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def _not_yet(step: dict) -> dict:
    return {
        "step": step["id"],
        "title": step["title"],
        "ticket": step["ticket"],
    }


def run(
    home: Path,
    *,
    resolved: dict | None = None,
    reader=None,
    out=None,
    isatty=None,
    pick_folder=None,
    write_probe=None,
    lark_runner=None,
    lark_install=None,
    transport_send=None,
    transport_events=None,
    facility_create=None,
    facility_attach=None,
    find_same_name=None,
    service=None,
    update_kb=None,
    clock=None,
    sleep=None,
) -> dict:
    """Run the initialization step machine from the first unfinished step.

    The order of guards is deliberate: a running service is refused before any
    prompt or write, then a non-interactive stdin is refused with the launcher
    hint.  Each completed step is persisted before the next question is asked.

    The Lark step's subprocess calls go through ``lark_runner`` (default
    ``subprocess.run``), its private installer through ``lark_install`` (default
    :func:`hostrun.lark.install_cli`), and its 15-minute wait through ``clock``
    and ``sleep``.  The transport test's sends go through ``transport_send``
    (default :func:`send_transport_test`) and its card callback through
    ``transport_events`` (default :func:`wait_card_callback`, whose own seam is
    the lark event source).  The facility creation goes through
    ``find_same_name``/``facility_create``/``facility_attach`` (defaults:
    :func:`default_find_same_name`, :func:`hostrun.facilities.create`,
    :func:`hostrun.facilities.attach`), the completion through ``service``
    (default a :class:`hostrun.console_service.Service` for the same runtime
    area) and the knowledge-base handoff through ``update_kb`` (default ``None``:
    the step ends with the exact next command instead of starting a read).
    Every one of them is injectable so no test ever spawns a real ``lark-cli``,
    npm, a system terminal or a live Feishu tenant.
    """
    home = Path(home)
    out = sys.stdout if out is None else out
    reader = reader or (lambda prompt="": input())
    pick = pick_folder or folder_dialog
    probe = write_probe or probe_write
    lark_runner = lark_runner or subprocess.run
    lark_install = lark_install or lark.install_cli
    transport_send = transport_send or send_transport_test
    transport_events = transport_events or wait_card_callback
    facility_create = facility_create or facilities_mod.create
    facility_attach = facility_attach or facilities_mod.attach
    find_same_name = find_same_name or default_find_same_name
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    code = _code_dir(home, resolved)
    default_archive = _default_archive(home, resolved)
    state = read_state(home)
    completed = [step for step in STEP_IDS if (state["steps"].get(step) or {}).get("status") == "ok"]
    base = {
        "action": "init",
        "state_path": str(state_path(home)),
        "completed": completed,
    }

    console_state = read_console_state(home)
    # US23 refuses a second init only while the *formal service* is running,
    # i.e. after initialization completed.  A fresh install has a resident
    # console whose service already reads "running" but nothing initialized
    # yet; that first /horizon-init must be allowed (B1, US14/US15).
    if console_state.get("service") == "running" and is_complete(home):
        return {
            **base,
            "ok": False,
            "reason": "service_running",
            "text": (
                "服务正在运行，拒绝重跑初始化。"
                "请先停止服务：执行 /horizon-stop 暂停后即可继续 /horizon-init。"
            ),
        }

    tty = False
    try:
        tty = bool((isatty or _default_isatty)())
    except Exception:  # noqa: BLE001 - an undetectable stdin is not a TTY
        tty = False
    if not tty:
        launcher = console_launcher_path(code)
        return {
            **base,
            "ok": False,
            "reason": "not_a_tty",
            "text": (
                "初始化需要交互式终端；请双击 "
                f"{launcher} 启动控制台，或在一个真实的终端窗口里运行 "
                "python -m hostrun.cli console。"
            ),
        }

    not_yet: list[dict] = []
    try:
        for step in STEPS:
            step_id = step["id"]
            if not step["implemented"]:
                not_yet.append(_not_yet(step))
                _say(out, f"[尚未实现] {step['title']}（由 {step['ticket']} 接入，本次不执行）")
                continue
            entry = state["steps"].get(step_id) or {}
            if entry.get("status") == "ok":
                value = entry.get("value") or {}
                completed_line = _completed_line(step_id, value)
                _say(out, f"[已完成] {step['title']}：{completed_line}")
                continue
            if step_id == "archive":
                outcome = _step_archive(
                    home,
                    code,
                    default_archive,
                    reader=reader,
                    out=out,
                    pick_folder=pick,
                    write_probe=probe,
                )
            elif step_id == "agent":
                archive = Path(
                    (state["steps"].get("archive") or {}).get("value", {}).get("path")
                    or default_archive
                )
                outcome = _step_agent(home, code=code, archive=archive, reader=reader, out=out)
            elif step_id == "naming":
                outcome = _step_naming(
                    home,
                    code=code,
                    reader=reader,
                    out=out,
                    find_same_name=find_same_name,
                    facility_create=facility_create,
                    facility_attach=facility_attach,
                )
            elif step_id == "push_time":
                outcome = _step_push_time(home, reader=reader, out=out)
            elif step_id == "knowledge":
                outcome = _step_knowledge(home, reader=reader, out=out)
            elif step_id == "transport":
                outcome = _step_transport(
                    home,
                    reader=reader,
                    out=out,
                    runner=lark_runner,
                    send=transport_send,
                    consume=transport_events,
                )
            else:
                outcome = _step_lark(
                    home,
                    code=code,
                    reader=reader,
                    out=out,
                    runner=lark_runner,
                    install=lark_install,
                    clock=clock,
                    sleep=sleep,
                )
            if not outcome.get("ok"):
                return {
                    **base,
                    "ok": False,
                    "reason": outcome.get("reason") or "step_failed",
                    "text": outcome.get("text") or "初始化未完成。",
                    "completed": completed_steps(home),
                    "next_step": step_id,
                    "not_yet": not_yet,
                    "step": outcome,
                }
            state = read_state(home)
    except _Aborted:
        return {
            **base,
            "ok": False,
            "reason": "aborted",
            "text": "初始化已中止；已完成的步骤已保存，下次 /horizon-init 从下一步继续。",
            "completed": completed_steps(home),
            "next_step": next(
                (
                    step["id"]
                    for step in STEPS
                    if step["implemented"]
                    and (read_state(home)["steps"].get(step["id"]) or {}).get("status") != "ok"
                ),
                None,
            ),
            "not_yet": not_yet,
        }
    except KeyboardInterrupt:
        return {
            **base,
            "ok": False,
            "reason": "interrupted",
            "text": "初始化已中止；已完成的步骤已保存，下次 /horizon-init 从下一步继续。",
            "completed": completed_steps(home),
            "not_yet": not_yet,
        }

    state = read_state(home)
    completed = [step for step in STEP_IDS if (state["steps"].get(step) or {}).get("status") == "ok"]
    next_step = next((item["step"] for item in not_yet), None)
    if next_step:
        text = (
            "已完成的步骤已保存；后续步骤尚未接入："
            + "、".join(f"{item['title']}（{item['ticket']}）" for item in not_yet)
            + "。再次运行 /horizon-init 会跳过已完成的步骤。"
        )
        return {
            **base,
            "ok": True,
            "reason": "not_yet",
            "completed": completed,
            "next_step": next_step,
            "not_yet": not_yet,
            "text": text,
        }

    if state.get("complete"):
        gate = console_gate.knowledge_gate(home)
        return {
            **base,
            "ok": True,
            "reason": "already_complete",
            "completed": completed,
            "next_step": None,
            "not_yet": not_yet,
            "knowledge_gate": gate,
            "text": (
                f"初始化已于 {state.get('completed_at')} 完成，本次未重复任何创建或启动动作。"
                f"服务：{read_console_state(home).get('service') or 'unknown'}；"
                f"知识库门禁：{'已就绪' if gate.get('ready') else '待初始化知识库'}。"
            ),
            "state": {
                "complete": True,
                "completed_at": state.get("completed_at"),
                "knowledge": state.get("knowledge") or "",
                "archive": state.get("archive") or "",
            },
        }

    lifecycle = service if service is not None else console_service.Service(home)
    finalized = _finalize(
        home,
        code=code,
        state=state,
        service=lifecycle,
        update_kb=update_kb,
        out=out,
        probe=probe,
    )
    _say(out, finalized.get("text") or "")
    return {
        **base,
        "ok": bool(finalized.get("ok")),
        "reason": finalized.get("reason") or "",
        "completed": completed_steps(home),
        "next_step": None,
        "not_yet": not_yet,
        "text": finalized.get("text") or "",
        "archive": finalized.get("archive"),
        "service": finalized.get("service"),
        "handoff": finalized.get("handoff"),
        "knowledge_gate": finalized.get("knowledge_gate")
        or console_gate.knowledge_gate(home),
        "state": finalized.get("state")
        or {
            "complete": is_complete(home),
            "completed_at": read_state(home).get("completed_at"),
            "knowledge": read_state(home).get("knowledge") or "",
            "archive": read_state(home).get("archive") or "",
        },
        "step": finalized if not finalized.get("ok") else None,
    }


def read_console_state(home: Path) -> dict:
    """Late import keeps this module importable without the console's seams."""
    from hostrun import console

    return console.read_state(Path(home))


def _completed_line(step_id: str, value: dict) -> str:
    if step_id == "archive":
        suffix = "（默认）" if value.get("default") else ""
        return f"{value.get('path') or '未知'}{suffix}"
    if step_id == "agent":
        return f"内置 Pi：{value.get('provider', '')} / {value.get('model', '')}"
    if step_id == "lark":
        cli = value.get("cli") or {}
        identity = value.get("identity") or {}
        sides = "、".join(
            name for name in ("user", "bot") if identity.get(f"{name}_ready")
        ) or "未就绪"
        return f"{cli.get('path') or '未记录'}（身份：{sides}）"
    if step_id == "transport":
        owner = value.get("owner") or {}
        if value.get("channel") == "ok" and owner.get("verified"):
            return "通道测试通过，owner 已核实"
        return "通道测试未通过"
    if step_id == "naming":
        base = value.get("base") or {}
        tables = value.get("tables") or {}
        kind = "复用" if value.get("reused") else "新建"
        return (
            f"{base.get('name') or (value.get('names') or {}).get('base') or '未记录'}"
            f"（{kind}，{len(tables)} 张表）"
        )
    if step_id == "push_time":
        return f"每日 {value.get('push_time') or '未记录'}（用户本地时区）"
    if step_id == "knowledge":
        if value.get("choice") == "update_kb":
            return "已选择初始化知识库（进入 /horizon-update-kb）"
        return "暂不初始化知识库（每日推送被门禁挡住）"
    return "已完成"
