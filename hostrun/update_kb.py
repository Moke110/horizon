"""``/horizon-update-kb`` read side: copy, shard, read and report (ADR 0009 §8).

The console command brackets the whole operation with a service pause and a
restore, copies a chosen directory into ``<archive>/originals`` (md5 verified,
source never modified), registers the copies in the archive index, then splits
whole files into controlled shards and runs at most two read-only agent runs.
Each sub-run only returns candidate knowledge cards and propositions; the
console merges and de-duplicates them into the code-directory candidate area and
writes an honest "unread" report.  Nothing here writes the knowledge base.

Contracts owned by this module (other tickets may read them):

- candidates: ``<runtime>/update-kb/candidates/candidates.json``
- unread report: ``<runtime>/update-kb/candidates/unread.json``
- progress: ``<runtime>/update-kb/state.json`` (shard status + phase)
- extracted text: ``<runtime>/update-kb/text/<md5>.txt``

Budget: whole files are packed to ``BATCH_RATIO`` (10%) of the character window;
a single file may exceed a batch, but a file above ``SINGLE_FILE_RATIO`` (50%)
of the window is reported ``未读取：超出读取上限`` instead of being read.

Incremental: a file whose md5 is already in :mod:`hostrun.archive_index` is
skipped and the skip is reported with its md5.  A shard left ``pending`` by an
interrupted run is resumed on the next invocation even though its file is now
registered, so an interruption never silently drops content.
"""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from common import ensure_dir, now, paper_id, read_json, slug, stable_id, write_json
from hostrun import archive_index

STATE_DIRNAME = "update-kb"
CANDIDATES_DIRNAME = "candidates"
TEXT_DIRNAME = "text"
STATE_FILENAME = "state.json"
CANDIDATES_FILENAME = "candidates.json"
UNREAD_FILENAME = "unread.json"
STATE_SCHEMA_VERSION = 1

#: Character estimate of the context window.  A launcher/agent may change it per
#: installation; tests inject a small window to make the budget visible.
CONTEXT_WINDOW_CHARS = 1_000_000
BATCH_RATIO = 0.10
SINGLE_FILE_RATIO = 0.50
MAX_CONCURRENCY = 2
READ_TIMEOUT_SECONDS = 900

UNREAD_LIMIT_REASON = "未读取：超出读取上限"
UNREAD_RETRY_REASON = "未读取：Agent 运行失败（已重试一次）"
UNREAD_PARSE_REASON = "未读取：候选输出无法解析（已重试一次）"

TEXT_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".csv",
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".rst",
        ".html",
        ".htm",
    }
)


# --------------------------------------------------------------------- paths
def state_dir(home: str | Path) -> Path:
    return Path(home) / STATE_DIRNAME


def state_path(home: str | Path) -> Path:
    return state_dir(home) / STATE_FILENAME


def candidates_dir(home: str | Path) -> Path:
    return state_dir(home) / CANDIDATES_DIRNAME


def candidates_path(home: str | Path) -> Path:
    return candidates_dir(home) / CANDIDATES_FILENAME


def unread_path(home: str | Path) -> Path:
    return candidates_dir(home) / UNREAD_FILENAME


def text_dir(home: str | Path) -> Path:
    return state_dir(home) / TEXT_DIRNAME


def _default_state() -> dict:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "phase": "idle",
        "source": "",
        "archive": "",
        "paused_before": False,
        "started_at": None,
        "updated_at": None,
        "shards": {},
        "unread": [],
        "candidate_cards": [],
        "candidate_propositions": [],
        "candidate_duplicates": 0,
        "completed": False,
    }


def read_state(home: str | Path) -> dict:
    stored = read_json(state_path(home)) or {}
    state = _default_state()
    if isinstance(stored, dict):
        state.update(stored)
    if not isinstance(state.get("shards"), dict):
        state["shards"] = {}
    for key in ("unread", "candidate_cards", "candidate_propositions"):
        if not isinstance(state.get(key), list):
            state[key] = []
    state["paused_before"] = bool(state.get("paused_before"))
    state["completed"] = bool(state.get("completed"))
    return state


def _write_state(home: str | Path, state: dict) -> dict:
    state["schema_version"] = STATE_SCHEMA_VERSION
    state["updated_at"] = now()
    write_json(state_path(home), state)
    return state


# -------------------------------------------------------------------- budget
def budget(window_chars: int = CONTEXT_WINDOW_CHARS) -> dict:
    """The two character thresholds: one batch and one single-file ceiling."""
    window = int(window_chars or CONTEXT_WINDOW_CHARS)
    if window <= 0:
        window = CONTEXT_WINDOW_CHARS
    batch_chars = max(1, int(window * BATCH_RATIO))
    single_chars = max(batch_chars, int(window * SINGLE_FILE_RATIO))
    return {
        "window_chars": window,
        "batch_chars": batch_chars,
        "single_file_chars": single_chars,
    }


# ------------------------------------------------------------------ identity
def business_id_for(path: str | Path) -> str:
    """Stable object id for a material file: ``pdf_…`` for PDFs, ``doc_…`` else."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pdf_ingest.ingest import paper_id_for

        return paper_id_for(path)
    return paper_id("doc", str(slug(path.stem)))


def source_files(source: str | Path) -> list[Path]:
    root = Path(source).expanduser().resolve()
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    )


def _md5(path: Path) -> str:
    return archive_index.file_md5(path)


def _copy_verified(source: Path, target: Path, expected: str) -> str:
    """Copy one file through a temporary name and verify the bytes by md5."""
    ensure_dir(target.parent)
    temporary = target.with_name(target.name + ".part")
    try:
        shutil.copy2(source, temporary)
        digest = _md5(temporary)
        if digest != expected:
            raise OSError(
                f"复制后 md5 不符：{digest} != {expected}（原文件 {source}）"
            )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return expected


# ------------------------------------------------------------------- upload
def _default_upload(runtime: str | Path, paper_id_value: str, path: str | Path) -> dict:
    """Upload one PDF through the existing library flow (live Feishu call)."""
    from library.service import Library

    library = Library(Path(runtime))
    record = {"title": Path(path).stem, "source": "update-kb"}
    return library.put_source(paper_id_value, Path(path), record=record)


# --------------------------------------------------------------- copy/import
def import_files(
    home: str | Path,
    archive: str | Path,
    source: str | Path,
    *,
    upload=None,
    emit=None,
) -> dict:
    """Copy every not-yet-registered source file into ``originals/``.

    The archive keeps ``originals/<business id>/<file name>``.  Existing bytes
    are never overwritten: a destination with a different md5 is reported as an
    unread conflict instead.  PDFs are then uploaded through the library seam,
    and the final local ledger registration makes a repeat run incremental.
    """
    home = Path(home)
    archive = Path(archive).expanduser().resolve()
    source = Path(source).expanduser().resolve()
    ledger = archive_index.load(home)
    known = {str(row.get("md5") or ""): row for row in ledger if row.get("md5")}
    seen: set[str] = set()
    copied: list[dict] = []
    skipped: list[dict] = []
    duplicates: list[dict] = []
    conflicts: list[dict] = []
    uploads: list[dict] = []
    upload_failed: list[dict] = []

    for path in source_files(source):
        try:
            digest = _md5(path)
            size = path.stat().st_size
        except OSError as exc:
            conflicts.append(
                {
                    "source": str(path),
                    "reason": f"未读取：源文件不可读：{exc}",
                }
            )
            continue
        business = business_id_for(path)
        target = archive / "originals" / business / path.name
        entry = {
            "source": str(path),
            "archive_path": str(target),
            "business_id": business,
            "md5": digest,
            "size": size,
        }
        if digest in known:
            skipped.append({**entry, "registered": True})
            continue
        if digest in seen:
            duplicates.append({**entry, "reason": "内容与本次已选文件重复"})
            continue
        seen.add(digest)
        if target.exists():
            if target.is_file() and _md5(target) == digest:
                copied.append({**entry, "existing": True})
            else:
                conflicts.append(
                    {
                        **entry,
                        "reason": "未读取：目标原文已存在同名文件且内容不同",
                    }
                )
            continue
        try:
            _copy_verified(path, target, digest)
        except OSError as exc:
            conflicts.append({**entry, "reason": f"未读取：复制失败：{exc}"})
            continue
        copied.append({**entry, "existing": False})
        if path.suffix.lower() == ".pdf" and upload is not None:
            try:
                outcome = upload(home, business, target)
            except Exception as exc:  # noqa: BLE001 - one upload must not stop the run
                outcome = {
                    "ok": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            uploads.append({**entry, "upload": outcome})
            if not (isinstance(outcome, dict) and outcome.get("ok")):
                upload_failed.append(
                    {
                        "source": str(path),
                        "archive_path": str(target),
                        "reason": str((outcome or {}).get("reason") or "上传失败"),
                    }
                )
            if emit is not None:
                emit(
                    "INFO" if (outcome or {}).get("ok") else "WARN",
                    "update-kb",
                    f"上传 {path.name}：{'成功' if (outcome or {}).get('ok') else '失败'}",
                )

    index = None
    if copied or duplicates:
        index = archive_index.register(home, archive)
    new_entries = [
        entry
        for entry in copied
        if str(entry.get("md5") or "") not in {
            str(row.get("md5") or "") for row in ledger if row.get("md5")
        }
    ]
    return {
        "copied": copied,
        "skipped_registered": skipped,
        "duplicates": duplicates,
        "conflicts": conflicts,
        "upload_failed": upload_failed,
        "index": index,
        "new_entries": new_entries,
    }


# -------------------------------------------------------------- text measure
def _extract_text(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            from pdf_ingest.ingest import _extract_pages

            pages = _extract_pages(path)
        except Exception as exc:  # noqa: BLE001 - an unreadable PDF is a result
            return {"ok": False, "reason": f"未读取：PDF 文本提取失败：{exc}"}
        return {"ok": True, "text": "\n".join(pages)}
    if suffix in TEXT_SUFFIXES:
        try:
            return {"ok": True, "text": path.read_text(encoding="utf-8", errors="replace")}
        except OSError as exc:
            return {"ok": False, "reason": f"未读取：文本读取失败：{exc}"}
    return {"ok": False, "reason": "未读取：不支持的格式"}


def measure_entries(
    home: str | Path,
    entries: list[dict],
    *,
    limits: dict | None = None,
    text_reader=None,
) -> tuple[list[dict], list[dict]]:
    """Extract each file's text, persist it, and split readable from unread.

    ``text_reader`` is injectable so a test can drive a PDF-sized estimate
    without a real PDF; the default reads PDFs or UTF-8 text.
    """
    limits = limits or budget()
    reader = text_reader or _extract_text
    measured: list[dict] = []
    unread: list[dict] = []
    for entry in entries:
        path = Path(entry["archive_path"])
        extracted = reader(path)
        if not extracted.get("ok"):
            unread.append(
                {
                    **entry,
                    "chars": 0,
                    "reason": extracted.get("reason") or "未读取：无法提取文本",
                }
            )
            continue
        text = str(extracted.get("text") or "")
        chars = len(text)
        if not text.strip():
            unread.append({**entry, "chars": 0, "reason": "未读取：没有可提取文本"})
            continue
        if chars > int(limits["single_file_chars"]):
            unread.append({**entry, "chars": chars, "reason": UNREAD_LIMIT_REASON})
            continue
        target = text_dir(home) / f"{entry['md5']}.txt"
        ensure_dir(target.parent)
        target.write_text(text, encoding="utf-8")
        measured.append({**entry, "chars": chars, "text_path": str(target)})
    return measured, unread


# ------------------------------------------------------------------- shards
def build_shards(measured: list[dict], limits: dict | None = None) -> list[dict]:
    """Pack whole files greedily in path order; an over-budget file gets its own."""
    limits = limits or budget()
    batch_chars = int(limits["batch_chars"])
    shards: list[dict] = []
    current: list[dict] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        paths = sorted(item["archive_path"] for item in current)
        shards.append(
            {
                "id": stable_id("shard", str(current_chars), *paths),
                "files": list(current),
                "chars": current_chars,
            }
        )
        current = []
        current_chars = 0

    for entry in sorted(measured, key=lambda item: item["archive_path"]):
        chars = int(entry.get("chars") or 0)
        if chars > batch_chars:
            flush()
            current = [entry]
            current_chars = chars
            flush()
            continue
        if current and current_chars + chars > batch_chars:
            flush()
        current.append(entry)
        current_chars += chars
    flush()
    return shards


def _state_shard(shard: dict, status: str, *, reason: str = "") -> dict:
    return {
        "id": shard["id"],
        "files": [
            {
                "source": item.get("source", ""),
                "archive_path": item["archive_path"],
                "business_id": item.get("business_id", ""),
                "md5": item.get("md5", ""),
                "chars": int(item.get("chars") or 0),
                "text_path": item.get("text_path", ""),
            }
            for item in shard["files"]
        ],
        "chars": int(shard.get("chars") or 0),
        "status": status,
        "reason": reason,
    }


def pending_shards(state: dict) -> list[dict]:
    pending: list[dict] = []
    for row in (state.get("shards") or {}).values():
        if not isinstance(row, dict) or row.get("status") != "pending":
            continue
        files = [item for item in row.get("files") or [] if item.get("archive_path")]
        if not files:
            continue
        pending.append({"id": row["id"], "files": files, "chars": int(row.get("chars") or 0)})
    return pending


# --------------------------------------------------------------- agent runs
def shard_task(shard: dict) -> str:
    lines = [
        "你是 horizon 的知识读取子运行，只读、不写。",
        "读取下列文件的内容，只产出候选知识卡片与命题；不要写入知识库，",
        "不要调用任何写库命令，也不要改动任何文件。",
        "只输出一个 JSON 对象，形如：",
        '{"cards":[{"id":"card_...","label":"","ontology":"","annotation":""}],'
        '"propositions":[{"id":"prop_...","title":"","review":"","related_knowledge":[],'
        '"evidence":[]}]}',
        "",
        "文件：",
    ]
    for entry in shard["files"]:
        lines.append(
            f"- {entry['archive_path']}（提取文本：{entry.get('text_path', '')}，"
            f"约 {int(entry.get('chars') or 0)} 字符）"
        )
    lines.extend(
        [
            "",
            "要求：只以这些文件的正文作证据；跨文件去重；卡片与命题使用中文；",
            "无法形成候选时输出空数组。",
        ]
    )
    return "\n".join(lines)


def parse_candidates(final_text: str) -> tuple[list[dict], list[dict]]:
    text = str(final_text or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the agent output")
    payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("agent output is not a JSON object")
    cards = payload.get("cards") or []
    propositions = payload.get("propositions") or []
    if not isinstance(cards, list) or not isinstance(propositions, list):
        raise ValueError("cards/propositions must be lists")
    cards = [item for item in cards if isinstance(item, dict)]
    propositions = [item for item in propositions if isinstance(item, dict)]
    return cards, propositions


def _call_agent(runner, home: Path, task: str, *, should_stop=None) -> dict:
    try:
        result = runner(
            home,
            task,
            kind="kb_read",
            lease=False,
            single_flight=False,
            timeout_seconds=READ_TIMEOUT_SECONDS,
            should_stop=should_stop,
        )
    except Exception as exc:  # noqa: BLE001 - one run failure is a shard failure
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        return {"ok": False, "reason": "agent returned no result"}
    if not result.get("ok"):
        return {"ok": False, "reason": str(result.get("reason") or "agent_failed")}
    try:
        cards, propositions = parse_candidates(result.get("final_text") or "")
    except (ValueError, TypeError) as exc:
        return {"ok": False, "reason": f"candidate_parse_failed: {exc}"}
    return {
        "ok": True,
        "cards": cards,
        "propositions": propositions,
        "session_id": result.get("session_id"),
        "run_id": result.get("run_id"),
    }


def _run_shard(
    home: Path,
    shard: dict,
    *,
    runner,
    should_stop=None,
) -> dict:
    """Run one shard, retrying once; return done/unread/pending with attempts."""
    task = shard_task(shard)
    attempts = 0
    last: dict = {}
    while attempts < 2:
        attempts += 1
        last = _call_agent(runner, home, task, should_stop=should_stop)
        if last.get("ok"):
            return {
                "status": "done",
                "cards": last["cards"],
                "propositions": last["propositions"],
                "attempts": attempts,
                "run_id": last.get("run_id"),
                "session_id": last.get("session_id"),
            }
        if should_stop is not None and should_stop():
            return {
                "status": "pending",
                "reason": "interrupted",
                "attempts": attempts,
            }
    reason = str(last.get("reason") or "")
    unread_reason = (
        UNREAD_PARSE_REASON if reason.startswith("candidate_parse_failed") else UNREAD_RETRY_REASON
    )
    return {"status": "unread", "reason": unread_reason, "attempts": attempts}


def run_shards(
    home: str | Path,
    shards: list[dict],
    *,
    runner,
    max_concurrency: int = MAX_CONCURRENCY,
    should_stop=None,
    on_result=None,
) -> tuple[dict, list[dict]]:
    """Run shards with at most ``max_concurrency`` in flight, submitted serially.

    Returns ``(results by shard id, shards left pending)``.  A stop request stops
    *submitting* new shards, joins the two already running, and leaves the rest
    pending for the next invocation.
    """
    home = Path(home)
    workers = max(1, min(int(max_concurrency or 1), MAX_CONCURRENCY))
    pending = list(shards)
    active: list[tuple[dict, threading.Thread, dict]] = []
    results: dict[str, dict] = {}
    stopping = False

    def stopped() -> bool:
        try:
            return bool(should_stop is not None and should_stop())
        except Exception:  # noqa: BLE001 - a broken stop check must not wedge
            return False

    def submit(shard: dict) -> None:
        holder: dict = {}

        def work() -> None:
            holder["result"] = _run_shard(
                home, shard, runner=runner, should_stop=should_stop
            )

        worker = threading.Thread(target=work, name="update-kb-read", daemon=True)
        worker.start()
        active.append((shard, worker, holder))

    def settle(index: int) -> None:
        shard, worker, holder = active.pop(index)
        worker.join()
        result = holder.get("result") or {"status": "pending", "reason": "interrupted"}
        results[shard["id"]] = result
        if on_result is not None:
            on_result(shard, result)

    while active or (pending and not stopping):
        while pending and len(active) < workers and not stopping:
            submit(pending.pop(0))
            if stopped():
                stopping = True
        if not active:
            break
        settle(0)
        if stopped():
            stopping = True
    while active:
        settle(0)
    return results, pending


# ------------------------------------------------------------------ merging
def _item_key(prefix: str, item: dict) -> str:
    identifier = str(item.get("id") or "").strip()
    if identifier:
        return identifier
    payload = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
    return stable_id(prefix, payload)


def merge_candidates(
    previous_cards: list[dict],
    previous_propositions: list[dict],
    outputs: list[dict],
) -> dict:
    """Merge candidate batches by id (or content hash), keeping the first copy."""
    cards: list[dict] = []
    propositions: list[dict] = []
    seen: set[str] = set()
    duplicates = 0
    batches = [(previous_cards, previous_propositions)] + [
        (result.get("cards") or [], result.get("propositions") or [])
        for result in outputs
        if result.get("status") == "done"
    ]
    for card_batch, proposition_batch in batches:
        for item in card_batch:
            key = "card:" + _item_key("card", item)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            cards.append(item)
        for item in proposition_batch:
            key = "prop:" + _item_key("prop", item)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            propositions.append(item)
    return {
        "cards": cards,
        "propositions": propositions,
        "duplicates": duplicates,
    }


# ------------------------------------------------------------------- reports
def unread_text(rows: list[dict]) -> str:
    if not rows:
        return "未读取清单：无（全部文件已读取）"
    lines = ["未读取清单："]
    for row in rows:
        lines.append(f"- {row.get('source') or row.get('archive_path')}：{row.get('reason', '')}")
    return "\n".join(lines)


def write_reports(home: str | Path, *, candidates: dict, unread: list[dict], source: str) -> dict:
    target = candidates_path(home)
    ensure_dir(target.parent)
    payload = {
        "schema_version": STATE_SCHEMA_VERSION,
        "generated_at": now(),
        "source": source,
        "cards": candidates.get("cards") or [],
        "propositions": candidates.get("propositions") or [],
        "duplicate_candidates": int(candidates.get("duplicates") or 0),
    }
    write_json(target, payload)
    unread_target = unread_path(home)
    write_json(
        unread_target,
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "generated_at": now(),
            "source": source,
            "count": len(unread),
            "items": unread,
        },
    )
    return {
        "candidates_path": str(target),
        "unread_path": str(unread_target),
        "cards": len(payload["cards"]),
        "propositions": len(payload["propositions"]),
    }


def _dedupe_unread(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for row in rows:
        key = (
            str(row.get("archive_path") or row.get("source") or ""),
            str(row.get("reason") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


# ------------------------------------------------------------------ directory
def choose_directory(initial: str | None = None) -> str | None:
    """The Windows folder picker; returns ``None`` when the user cancels."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        try:
            root.withdraw()
            root.attributes("-topmost", True)
            selected = filedialog.askdirectory(
                title="选择资料目录",
                initialdir=initial or "",
                mustexist=True,
            )
        finally:
            root.destroy()
        return str(selected) if selected else None
    except Exception:  # noqa: BLE001 - a missing GUI simply cancels the choice
        return None


def _resolve_archive(home: Path) -> str:
    from hostrun import layout

    return str(layout.resolve(code=Path(home).resolve().parent)["archive"])


def _valid_source(source: Path, archive: Path) -> str:
    if not source.is_dir():
        return "source_missing"
    try:
        source.relative_to(archive)
    except ValueError:
        pass
    else:
        return "source_inside_archive"
    try:
        archive.relative_to(source)
    except ValueError:
        return ""
    return "source_contains_archive"


# ----------------------------------------------------------------------- run
def run(
    home: str | Path,
    *,
    source_dir: str | Path | None = None,
    archive: str | Path | None = None,
    service=None,
    window_chars: int = CONTEXT_WINDOW_CHARS,
    upload=None,
    run_agent=None,
    choose_directory_fn=None,
    should_stop=None,
    emit=None,
    max_concurrency: int = MAX_CONCURRENCY,
    text_reader=None,
    restore_service: bool = True,
) -> dict:
    """Pause, copy, shard, read (max 2), merge candidates, report unread, restore.

    ``source_dir`` skips the picker.  With no source but pending shards from an
    interrupted run, the previous source is reused and only the pending shards
    are read.  ``upload``/``run_agent`` are the offline seams; the defaults call
    the live library and the agent-run adapter.  ``restore_service=False`` keeps
    the pause in place so the chained per-item review owns the restore; the
    console uses that to keep the scheduler quiet for the whole review (US25).
    """
    home = Path(home).expanduser().resolve()
    ensure_dir(state_dir(home))
    archive_path = (
        Path(archive).expanduser().resolve() if archive else Path(_resolve_archive(home))
    )
    ensure_dir(archive_path / "originals")
    state = read_state(home)
    limits = budget(window_chars)
    runner = run_agent
    if runner is None:
        from hostrun import agent_run

        runner = agent_run.run
    uploader = upload if upload is not None else _default_upload

    def log(level: str, message: str) -> None:
        if emit is not None:
            try:
                emit(level, "update-kb", message)
            except Exception:  # noqa: BLE001 - reporting never stops the read
                pass

    previous_pending = pending_shards(state)
    source: Path | None = None
    if source_dir is not None:
        source = Path(source_dir).expanduser().resolve()
        invalid = _valid_source(source, archive_path)
        if invalid:
            return {
                "ok": False,
                "action": "update_kb",
                "reason": invalid,
                "text": f"无法读取该目录：{source}（{invalid}）",
                "paused_before": False,
                "restored": False,
                "copied": [],
                "skipped_registered": [],
                "unread": [],
                "shards": [],
                "pending_shards": len(previous_pending),
            }
    elif previous_pending and state.get("source"):
        # Resume uses the source of the interrupted run; a fresh run always asks
        # for a directory again (ADR 0009 §8 step 2).
        candidate = Path(str(state.get("source"))).expanduser()
        if candidate.is_dir():
            source = candidate.resolve()

    # The pause brackets the whole operation, including the folder dialog.
    prior_paused = _state_paused(home)
    lifecycle = service
    if lifecycle is None:
        from hostrun import console_service

        lifecycle = console_service.Service(home)
    try:
        paused = lifecycle.pause()
    except Exception as exc:  # noqa: BLE001 - a broken service is reported
        return {
            "ok": False,
            "action": "update_kb",
            "reason": "pause_failed",
            "text": f"暂停服务失败：{type(exc).__name__}: {exc}",
            "paused_before": prior_paused,
            "restored": False,
            "copied": [],
            "skipped_registered": [],
            "unread": [],
            "shards": [],
            "pending_shards": len(previous_pending),
        }
    if not (isinstance(paused, dict) and paused.get("ok", True)):
        return {
            "ok": False,
            "action": "update_kb",
            "reason": "pause_failed",
            "text": "暂停服务失败，未读取任何文件。",
            "paused_before": prior_paused,
            "restored": False,
            "copied": [],
            "skipped_registered": [],
            "unread": [],
            "shards": [],
            "pending_shards": len(previous_pending),
        }

    restored = False
    restore: dict = {}
    final_result: dict | None = None
    try:
        if source is None:
            if previous_pending:
                log("INFO", f"续接上次未完成的分片：{len(previous_pending)}")
            else:
                picker = choose_directory_fn or choose_directory
                try:
                    chosen = picker()
                except Exception as exc:  # noqa: BLE001 - a cancelled dialog is a result
                    log("WARN", f"目录选择失败：{type(exc).__name__}: {exc}")
                    chosen = None
                if not chosen:
                    log("INFO", "未选择目录，已取消本次知识库更新")
                    final_result = {
                        "ok": False,
                        "action": "update_kb",
                        "reason": "cancelled",
                        "text": "未选择目录，已取消；服务状态已恢复。",
                        "paused_before": prior_paused,
                        "restored": False,
                        "copied": [],
                        "skipped_registered": [],
                        "unread": [],
                        "shards": [],
                        "pending_shards": len(previous_pending),
                    }
                    return final_result
                source = Path(chosen).expanduser().resolve()
                invalid = _valid_source(source, archive_path)
                if invalid:
                    final_result = {
                        "ok": False,
                        "action": "update_kb",
                        "reason": invalid,
                        "text": f"无法读取该目录：{source}（{invalid}）",
                        "paused_before": prior_paused,
                        "restored": False,
                        "copied": [],
                        "skipped_registered": [],
                        "unread": [],
                        "shards": [],
                        "pending_shards": len(previous_pending),
                    }
                    return final_result

        log("INFO", f"已暂停服务，开始读取资料目录：{source or '(续接分片)'}")
        state.update(
            {
                "phase": "importing",
                "source": str(source) if source else str(state.get("source") or ""),
                "archive": str(archive_path),
                "paused_before": prior_paused,
                "started_at": state.get("started_at") or now(),
                "completed": False,
            }
        )
        _write_state(home, state)

        if source is not None:
            import_result = import_files(
                home, archive_path, source, upload=uploader, emit=emit
            )
        else:
            import_result = {
                "copied": [],
                "skipped_registered": [],
                "duplicates": [],
                "conflicts": [],
                "upload_failed": [],
                "index": None,
                "new_entries": [],
            }
        new_entries = import_result["new_entries"]
        log(
            "INFO",
            f"复制完成：新增 {len(import_result['copied'])}，"
            f"已登记跳过 {len(import_result['skipped_registered'])}，"
            f"冲突 {len(import_result['conflicts'])}",
        )

        measured, unread = measure_entries(
            home, new_entries, limits=limits, text_reader=text_reader
        )
        shards = build_shards(measured, limits)
        if previous_pending:
            shards = previous_pending + shards
            log("INFO", f"续接上次未完成的分片：{len(previous_pending)}")
        state["shards"] = {shard["id"]: _state_shard(shard, "pending") for shard in shards}
        state["phase"] = "reading"
        _write_state(home, state)

        def on_result(shard: dict, result: dict) -> None:
            row = _state_shard(shard, result.get("status") or "pending", reason=result.get("reason", ""))
            state["shards"][shard["id"]] = row
            if result.get("status") == "done":
                log(
                    "INFO",
                    f"分片完成：{len(shard['files'])} 个文件、{shard['chars']} 字符",
                )
            elif result.get("status") == "unread":
                log("WARN", f"分片跳过：{result.get('reason')}")
            _write_state(home, state)

        results, still_pending = run_shards(
            home,
            shards,
            runner=runner,
            max_concurrency=max_concurrency,
            should_stop=should_stop,
            on_result=on_result,
        )

        merged = merge_candidates(
            state.get("candidate_cards") or [],
            state.get("candidate_propositions") or [],
            list(results.values()),
        )
        for shard in still_pending:
            state["shards"][shard["id"]] = _state_shard(shard, "pending")
        for shard in shards:
            outcome = results.get(shard["id"])
            if outcome and outcome.get("status") == "unread":
                unread.extend(
                    {
                        **entry,
                        "reason": outcome.get("reason") or UNREAD_RETRY_REASON,
                    }
                    for entry in shard["files"]
                )

        unread = _dedupe_unread(
            [
                *[{**row, "reason": row.get("reason", "")} for row in state.get("unread") or []],
                *import_result["conflicts"],
                *unread,
            ]
        )
        source_text = str(source) if source else str(state.get("source") or "")
        reports = write_reports(home, candidates=merged, unread=unread, source=source_text)
        state["unread"] = unread
        state["candidate_cards"] = merged["cards"]
        state["candidate_propositions"] = merged["propositions"]
        state["candidate_duplicates"] = merged["duplicates"]
        state["completed"] = not still_pending
        state["phase"] = "done" if not still_pending else "interrupted"
        _write_state(home, state)

        if still_pending:
            log("WARN", f"读取被中断，剩余 {len(still_pending)} 个分片留待下次续接")
        else:
            log("INFO", f"候选合并完成：卡片 {reports['cards']}，命题 {reports['propositions']}")
        final_result = {
            "ok": not still_pending,
            "action": "update_kb",
            "reason": "done" if not still_pending else "interrupted",
            "text": _summary_text(
                import_result,
                shards,
                results,
                unread,
                reports,
                still_pending,
                concurrency=max_concurrency,
            ),
            "source": source_text,
            "archive": str(archive_path),
            "paused_before": prior_paused,
            "restored": False,
            "copied": import_result["copied"],
            "skipped_registered": import_result["skipped_registered"],
            "duplicates": import_result["duplicates"],
            "conflicts": import_result["conflicts"],
            "upload_failed": import_result["upload_failed"],
            "index": import_result["index"],
            "shards": [
                {
                    "id": shard["id"],
                    "files": [entry["archive_path"] for entry in shard["files"]],
                    "chars": shard["chars"],
                }
                for shard in shards
            ],
            "unread": unread,
            "unread_text": unread_text(unread),
            "pending_shards": len(still_pending),
            "duplicate_candidates": merged["duplicates"],
            **reports,
            "state_path": str(state_path(home)),
        }
        return final_result
    finally:
        try:
            if not restore_service:
                # The chained review (#90) restores immediately before its
                # first-push drill; restoring here would reopen the scheduler
                # during the human-paced review.
                restore = {"ok": True, "action": "deferred", "paused": True}
                restored = True
            elif prior_paused:
                # The service was already paused before this run; the first
                # pause() call above left it that way, so do not log a second one.
                restore = {"ok": True, "action": "pause_kept"}
                restored = True
            else:
                restore = lifecycle.start()
                restored = bool(isinstance(restore, dict) and restore.get("ok", True))
        except Exception as exc:  # noqa: BLE001 - restoration is reported, not raised
            restore = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
            restored = False
        if final_result is not None:
            final_result["restored"] = restored
            final_result["restore"] = restore
        try:
            latest = read_state(home)
            latest["phase"] = latest.get("phase") if latest.get("phase") in {"done", "interrupted"} else "idle"
            latest["restored"] = restored
            _write_state(home, latest)
        except OSError:
            pass


def _state_paused(home: Path) -> bool:
    from hostrun import console

    return bool(console.read_state(home).get("paused"))


def _summary_text(
    import_result: dict,
    shards: list[dict],
    results: dict,
    unread: list[dict],
    reports: dict,
    pending: list[dict],
    *,
    concurrency: int = MAX_CONCURRENCY,
) -> str:
    lines = [
        f"资料复制：新增 {len(import_result['copied'])}，"
        f"已登记跳过 {len(import_result['skipped_registered'])}，"
        f"重复 {len(import_result['duplicates'])}，冲突 {len(import_result['conflicts'])}",
        f"读取分片：{len(shards)}（并发上限 {min(int(concurrency or 1), MAX_CONCURRENCY)}）",
        f"候选卡片：{reports['cards']}，命题：{reports['propositions']}，"
        f"去重：{reports.get('duplicate_candidates', 0)}",
    ]
    if import_result["upload_failed"]:
        lines.append(f"上传失败：{len(import_result['upload_failed'])}")
    if pending:
        lines.append(f"未完成分片：{len(pending)}（再次执行可续接）")
    lines.append(unread_text(unread))
    return "\n".join(lines)
