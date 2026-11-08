#!/usr/bin/env python3
"""Offline end-to-end demo: the current pipeline, file contracts only.

  checkpoint -> horizon.plan -> retriever(fixture) -> editor.rank
  -> writer task/check -> archive.add-push -> publisher.send(dry-run)
  -> rating card event -> unified queue -> 逐题同意 -> archive.apply_group

A template push has no seven sections, so the demo renders a minimal HTML
instead of calling writer render; the real seven-section HTML is written by the
host agent. Nothing is applied to the knowledge base before an explicit
agreement, which is exactly what step 10 shows.

Runs in a throwaway code directory; never touches the real runtime area.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def step(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def run(args: list[str], env: dict, quiet: bool = False) -> dict:
    print(f"$ {' '.join(args)}")
    proc = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(args)}")
    out = proc.stdout.strip()
    if out and not quiet:
        text = out if len(out) < 1600 else out[:1600] + "\n…"
        print(text)
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}


def main() -> int:
    code = Path(tempfile.mkdtemp(prefix="kl-demo-")) / "code"
    home = code / ".horizon"
    shutil.copytree(ROOT / "fixtures" / "home", home, dirs_exist_ok=True)
    env = {
        **os.environ,
        "HORIZON_CODE": str(code),
        "HORIZON_ARCHIVE": str(code.parent / "horizon-archive"),
        "HORIZON_RUNTIME": str(home),
    }
    print(f"运行时区 = {home}")

    step("1. archive: 四层存储（fixtures 已预置知识卡片 + 命题）")
    run([PY, "-m", "archive.cli", "reindex"], env)
    run([PY, "-m", "archive.cli", "stats"], env)

    step("2. horizon: 知识卡片 -> 检索式")
    run([PY, "-m", "horizon.cli", "validate"], env)
    run(
        [
            PY, "-m", "horizon.cli", "plan", "--date", "2026-10-20",
            "--days", "7", "--out", str(home / "plan.json"),
        ],
        env,
    )

    step("3. retriever: 离线取原文（fixture 源）")
    run(
        [
            PY, "-m", "retriever.cli", "fetch", "--plan", str(home / "plan.json"),
            "--out", str(home / "candidates.jsonl"), "--source", "fixture",
            "--fixtures", "fixtures/papers",
        ],
        env,
    )

    step("4. archive: 候选入原文层（去重）")
    ingest_dir = home / "_ingest"
    ingest_dir.mkdir(exist_ok=True)
    for index, line in enumerate((home / "candidates.jsonl").read_text().splitlines(), start=1):
        if not line.strip():
            continue
        path = ingest_dir / f"{index}.json"
        path.write_text(line, encoding="utf-8")
        run([PY, "-m", "archive.cli", "add-paper", "--file", str(path)], env, quiet=True)
    run([PY, "-m", "archive.cli", "stats"], env)

    step("5. editor: 冷启动排序（无评分）+ fit")
    run([PY, "-m", "editor.cli", "fit"], env)
    run(
        [
            PY, "-m", "editor.cli", "rank", "--candidates", str(home / "candidates.jsonl"),
            "--date", "2026-10-20", "--limit", "4", "--per-checkpoint", "2",
            "--out", str(home / "selection.json"),
        ],
        env,
    )
    run(
        [
            PY, "-m", "editor.cli", "task", "--candidates", str(home / "candidates.jsonl"),
            "--date", "2026-10-20", "--limit", "4", "--per-checkpoint", "2",
            "--out", str(home / "editor-task.json"),
        ],
        env,
    )
    run(
        [
            PY, "-m", "editor.cli", "check", "--task", str(home / "editor-task.json"),
            "--selection", str(home / "selection.json"),
        ],
        env,
    )

    step("6. writer: 四段式推送 + 知识更新提议")
    run(
        [
            PY, "-m", "writer.cli", "write", "--selection", str(home / "selection.json"),
            "--out", str(home / "outbox"),
        ],
        env,
    )
    run(
        [
            PY, "-m", "writer.cli", "task", "--selection", str(home / "selection.json"),
            "--out", str(home / "writer-task.json"),
        ],
        env,
    )
    run(
        [
            PY, "-m", "writer.cli", "check", "--task", str(home / "writer-task.json"),
            "--outbox", str(home / "outbox"),
        ],
        env,
    )

    step("7. archive: 推送入库")
    push_files = sorted((home / "outbox" / "pushes").glob("*.json"))
    pushes = []
    for path in push_files:
        pushes.append(json.loads(path.read_text()))
        run([PY, "-m", "archive.cli", "add-push", "--file", str(path)], env, quiet=True)
    chosen = max(pushes, key=lambda p: len(p.get("proposals", [])))
    print(f"\n选用推送：{chosen['push_id']}（{len(chosen.get('proposals', []))} 条提议）")
    print(chosen["body_md"])

    step("8. publisher: 发评分卡（dry-run，不发网络请求）")
    push_path = home / "outbox" / "pushes" / f"{chosen['push_id']}.json"
    html_path = home / "outbox" / f"{chosen['push_id']}.html"
    required_sections = ("takeaway", "background", "methods", "results", "why", "limits", "sources")
    sections = chosen.get("sections") or {}
    complete = all(str(sections.get(name) or "").strip() for name in required_sections) and "<figure" in str(
        chosen.get("body_html") or ""
    )
    if complete:
        run(
            [PY, "-m", "writer.cli", "render", "--push", str(push_path),
             "--format", "html", "--out", str(html_path)],
            env,
        )
    else:
        # 模板 fallback 只产出 body_md，没有七段 sections，render 会（也应该）拒绝。
        # 真实流程里七段 HTML 由宿主 agent 亲自写；这里给一个最小自包含 HTML 交差。
        print("（该推送来自模板 fallback，无 sections；跳过 render，用最小 HTML 代替）")
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(
            "<!doctype html><meta charset=\"utf-8\"><title>{}</title>"
            "<article><pre>{}</pre></article>".format(
                chosen["push_id"], chosen.get("body_md", "").replace("&", "&amp;").replace("<", "&lt;")
            ),
            encoding="utf-8",
        )
    send = run(
        [
            PY, "-m", "publisher.cli", "send", "--push", str(push_path),
            "--html", str(html_path),
            "--to", "ou_demo", "--dry-run",
        ],
        env,
    )

    step("9. publisher: 模拟点击「质量优秀」+ 评语")
    rating_event = {
        "message_id": send["rating_message_id"],
        "action_name": "score_high",
        "form_value": json.dumps({"comment": "接上了之前的 HMBS 线索，HTML 预览也清楚"}, ensure_ascii=False),
        "operator_id": "ou_demo",
        "token": "",
    }
    events = home / "events.ndjson"
    events.write_text(json.dumps(rating_event, ensure_ascii=False) + "\n", encoding="utf-8")
    run([PY, "-m", "publisher.cli", "ingest", "--events", str(events), "--offline"], env)

    step("10. queue: 评分之后的统一队列")
    queue_state = run([PY, "-m", "question_queue.cli", "status"], env, quiet=True)
    answered = [r for r in queue_state["items"] if r["status"] == "answered"]
    print(f"题目总数 {len(queue_state['items'])}，已答 {len(answered)}，"
          f"当前活动题 {queue_state.get('active_id')}")

    active = queue_state.get("active_id")
    if active:
        # 只有显式的「同意」才会写知识库；这里是逐题确认，不是审批卡批量通过
        run([PY, "-m", "question_queue.cli", "answer", "--item", active, "--decision", "agree_all"], env)
        after = run([PY, "-m", "question_queue.cli", "status"], env, quiet=True)
        print(f"同意后：已答 {len([r for r in after['items'] if r['status'] == 'answered'])} 题，"
              f"剩余活动题 {after.get('active_id')}")

    step("11. 结果")
    prop = json.loads(
        subprocess.run(
            [PY, "-m", "archive.cli", "prop-get", "prop_hmbs_aip"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        ).stdout
    )
    node = json.loads(
        subprocess.run(
            [PY, "-m", "archive.cli", "card-get", "card_hmbs"],
            cwd=ROOT, env=env, capture_output=True, text=True,
        ).stdout
    )
    evidence = prop.get("evidence") or []
    print(f"命题 prop_hmbs_aip 证据条数：{len(evidence)} -> {[e.get('id') for e in evidence]}")
    print(f"知识卡 card_hmbs ontology：{node['ontology']}")
    print(f"知识卡片数：{len(list((home / 'archive' / 'cards').glob('*.json')))}")
    print(f"命题数：{len(list((home / 'archive' / 'propositions').glob('*.json')))}")
    ratings = home / "ratings.jsonl"
    print(f"评分记录：{ratings.read_text().strip() if ratings.exists() else '（无）'}")
    print(f"\n全部产物在 {home}")
    print("可单独重跑任一模块，例如：")
    print(f"  python -m question_queue.cli status（沿用上面的 HORIZON_* 环境变量）")
    return 0




if __name__ == "__main__":
    raise SystemExit(main())
