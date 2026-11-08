# horizon 原型 · Agent 任务契约

> 实现范围：本文保留现有模块的字段、命令、校验和业务流程。产品定位与交互以[已确认决策](../docs/adr/0001-product-direction.md)为准；论文专用字段、飞书适配与现有确认门禁不构成新产品的全局限制。执行现有模块时仍遵守其校验，不据此绕过门禁。

本项目的 editor 与 writer **不内置模型调用**。它们把「需要判断」的部分打包成任务文件，由 Horizon 内置 Pi Agent 读取、推理、写回；脚本只负责取数、校验与落盘。这样换 agent 或换模型不需要改代码。

```
editor task  →  agent 排序  →  editor check  →  writer task  →  agent 写作  →  writer check
```

任务文件里已经包含 agent 需要的全部上下文（知识卡片的 ontology / annotation、关联命题、历史篇数），agent 也可以随时用 `archive.cli` 自行读取更多细节。

## 1. editor task → selection

`python -m editor.cli task --candidates candidates.jsonl --out editor-task.json`

```json
{
  "schema_version": 1,
  "kind": "editor-task",
  "date": "2026-10-20",
  "instructions": "……排序标准、输出要求……",
  "limits": { "limit": 5, "per_checkpoint": 2 },
  "preferences": {
    "checkpoints": { "card_hmbs": { "n": 3, "mean": 0.72 } },
    "sources": { "europepmc": { "n": 2, "mean": 0.6 } }
  },
  "checkpoints": {
    "card_hmbs": {
      "label": "HMBS",
      "annotation": "……判据……",
      "ontology": ["HMBS", "PBGD"],
      "weight": 6,
      "card": { "id": "card_hmbs", "label": "HMBS", "annotation": "……", "ontology": ["HMBS", "PBGD"] },
      "propositions": [ { "prop_id": "prop_hmbs_aip", "title": "…", "text": "……" } ],
      "archived_count": 12
    }
  },
  "candidates": [
    {
      "paper_id": "europepmc_42900001",
      "checkpoint_id": "card_hmbs",
      "source": "europepmc",
      "title": "……",
      "abstract": "……",
      "published": "2026-10-16",
      "is_preprint": false,
      "url": "https://…",
      "retrieval": { "query_id": "q_card_hmbs_europepmc", "rank_hint": 1 }
    }
  ]
}
```

Agent 写回 `selection.json`（形状见 `contracts/README.md` §4）：每条必须含 `checkpoint_id`、`paper.paper_id`、`score`（0–1）、`rationale`；`breakdown` 可选。不得编造候选之外的文章，不得超 `limits`。

校验：

```bash
python -m editor.cli check --task editor-task.json --selection selection.json
```

退出码 1 + `errors[]` 表示不合法；agent 应修正后重试，而不是绕过校验。

## 2. writer task → push

`python -m writer.cli task --selection selection.json --out writer-task.json`

```json
{
  "schema_version": 2,
  "kind": "writer-task",
  "format": "html",
  "date": "2026-10-21",
  "instructions": "……七段结构、HTML 片段、图片、引用、提议规则……",
  "archive_cli": "python -m archive.cli",
  "figure_command": "python -m writer.cli figures --selection <selection.json> --out <outbox>",
  "render_command": "python -m writer.cli render --push outbox/pushes/<push_id>.json --out <pdf>",
  "limits": { "targets": 2, "per_entity": 1 },
  "evidence_rules": {
    "paper_id": "must be one of push.paper_ids",
    "page": "integer >= 1, or null when the quote is from the abstract",
    "quote": "verbatim substring of the paper text/abstract, 8..500 chars"
  },
  "items": [
    {
      "checkpoint_id": "card_hmbs",
      "card_id": "card_hmbs",
      "paper": { "paper_id": "…", "title": "…", "abstract": "…" },
      "context": {
        "card": { "id": "card_hmbs", "label": "HMBS", "annotation": "…", "ontology": ["PBGD"] },
        "propositions": [
          {
            "id": "prop_hmbs_aip",
            "title": "…",
            "review": "## 结论\n…\n## 支持证据\n…\n## 反证与边界\n…\n## 待验证\n…",
            "related_knowledge": ["card_hmbs", "card_aip"],
            "evidence": [
              {
                "id": "ev_hmbs_aip_01",
                "stance": "supports",
                "conclusion": "≤200 字",
                "papers": [{ "paper_id": "…", "page": 3, "quote": "…", "added_at": "…" }],
                "created_at": "…",
                "updated_at": "…"
              }
            ],
            "evidence_updates_since_review": 3,
            "review_due": false
          }
        ],
        "cards": [ { "id": "card_aip", "label": "AIP", "ontology": ["…"], "annotation": "…" } ],
        "archived_count": 2,
        "recent_papers": [ { "paper_id": "…", "title": "…", "published": "…" } ]
      }
    }
  ],
  "proposal_kinds": {
    "add_card_ontology": "给现有卡片加 ontology 词：target_type=card，diff.path=ontology",
    "update_card_annotation": "改写卡片 annotation：target_type=card，diff.path=annotation",
    "create_card": "新建卡片：target_type=none，value=label+ontology+annotation",
    "create_proposition": "新建命题：target_type=none，value=title+review+related_knowledge（≥2 张已有卡）+evidence（≥2 条）",
    "update_proposition_title": "改命题标题：target_type=proposition，diff.path=title",
    "update_proposition_review": "改写综述：target_type=proposition，diff.path=review",
    "add_proposition_evidence": "新建证据条目：target_type=proposition，value={conclusion,stance,papers[]}",
    "merge_proposition_evidence": "并入已有证据：target_type=proposition，value={target_evidence_id,papers[],conclusion?}",
    "update_proposition_related_knowledge": "改命题关联卡片：target_type=proposition，diff.path=related_knowledge"
  },
  "output": {
    "dir": "outbox/pushes",
    "filename": "<push_id>.json",
    "schema": "contracts/README.md §5.1 + §9"
  }
}
```

Agent 先取图：

```bash
python -m writer.cli figures --selection selection.json --out outbox
# -> outbox/figures.json + outbox/pushes/assets/<push_id>/fig-*
```

然后为每个 item 写一个 push 文件，文件名 = `push_id`：

- `format: "pdf"`；
- `push_id = "<date>_<checkpoint_id>_<NN>"`（值是卡片 id，如 `2026-10-21_card_hmbs_01`），NN 从 01 递增（可用 task 里的 `suggested_push_id`）；
- `paper_ids` 必须包含该 item 的 `paper_id`，`paper` 里保留作者/期刊/日期/URL；
- `sections` 七个键 `takeaway / background / methods / results / why / limits / sources` 都非空；
- `body_html` 是正文 HTML 片段（不是整页），图片用 `<figure data-asset="fig-1"></figure>` 占位；
- `assets` 至少一项，来自 `figures.json`，`caption` 非空且 `source` 为 `paper | generated`；
- `why` 必须引用 `context`（知识卡片、命题、history、归档篇数至少其一）；
- `proposals` 只允许 §2.2 的 8 种 `op`，`status` 必须是 `draft`。

### 2.2 更新提议契约（v2）

每条 proposal 的公共字段：

```json
{
  "schema_version": 2,
  "proposal_id": "p_<push_id>_<NN>",
  "push_id": "…",
  "op": "add_proposition_evidence",
  "target_type": "proposition",
  "target_id": "prop_hmbs_aip",
  "diff": { "op": "add|replace", "path": "<字段>", "value": "…" },
  "before": "…",
  "after": "…",
  "rationale": "…（≥8 字）",
  "confidence": 0.7,
  "evidence": { "paper_id": "…", "page": 3, "quote": "…", "section": "results" },
  "status": "draft"
}
```

| `op` | 目标 | `diff.path` / `value` | `before` → `after` |
|---|---|---|---|
| `add_card_ontology` | 已有 card | `ontology` / 新词或词列表 | 当前列表 → 追加后列表 |
| `update_card_annotation` | 已有 card | `annotation` / 新文本 | 当前文本 → 新文本 |
| `create_card` | 无 | `card` / `{id?, label, ontology, annotation}` | `null` → 新卡片 |
| `create_proposition` | 无 | `proposition` / `{id?, title, review, related_knowledge, evidence[≥2]}` | `null` → 新命题 |
| `update_proposition_title` | 已有 prop | `title` / 新标题 | 当前标题 → 新标题 |
| `update_proposition_review` | 已有 prop | `review` / 新综述 | 当前综述 → 新综述（20–2000 字） |
| `add_proposition_evidence` | 已有 prop | `evidence` / `{conclusion≤200, stance, papers[]}` | 当前证据数 → +1 |
| `merge_proposition_evidence` | 已有 prop | `evidence` / `{target_evidence_id, papers[], conclusion?}` | 目标条目报道数 → +新增报道数 |
| `update_proposition_related_knowledge` | 已有 prop | `related_knowledge` / ≥2 卡 id | 当前列表 → 新列表 |

硬规则（`writer check` 会逐条校验）：

1. 每篇推送最多 **2 个实体目标**（知识卡片 + 命题合计，新建也占名额）；同一目标的多个操作合并成一个更新题，不重复占名额。
2. `before` 必须等于任务快照里的当前值（防陈旧 diff）；`after` 必须等于 `diff.value`（或按上表推导），且必须发生变化。
3. 证据每篇报道的 `paper_id` 必须是 `push.paper_ids` 之一；`page` 为 ≥1 整数或 `null`（摘要引文）；`quote` 8–500 字；`conclusion` 1–200 字；`stance ∈ supports|contradicts`。
4. `create_proposition.related_knowledge` ≥2 张**已存在**卡片；本次新建的卡片不能在同一 push 里被新命题引用；`create_proposition` 必须带 ≥2 条证据。
5. `create_card.id` / `create_proposition.id` 可省略（由 archive 按 label/title 生成），给定时必须符合 `card_<slug>` / `prop_<slug>`。
6. 目标必须存在于本 item 的 `context`；不得编造卡片、命题或论文。
7. `proposal_id` 在 push 内唯一，`status` 只能 `draft`；writer 永不直接写 `archive/`。

校验：

```bash
python -m writer.cli check --task writer-task.json --outbox outbox
```

渲染：

```bash
python -m writer.cli render --push outbox/pushes/<push_id>.json --out outbox/<push_id>.html
```

渲染器负责固定模板、中文字体、页眉页脚、图注与「原文图 / 生成图」标记；agent 不写 CSS。

校验通过后再入库：

```bash
python -m archive.cli add-push --file outbox/pushes/<push_id>.json
python -m library.cli ensure-source --paper <push 里的 paper_id>
```

入库后交给 publisher 入队并发送当前题：

```bash
python -m publisher.cli send --push outbox/pushes/<push_id>.json --html outbox/<push_id>.html --to ou_xxx
# 或只发评分/队首题：python -m publisher.cli rating --push ... --to ou_xxx
python -m question_queue.cli status
```

`add-push` 只写 archive；**入队发生在 `publisher send/rating`**。队列按实体把同一目标的多个 proposals 合并为一题，评分题在前、更新题在后；每题答完才发下一题。

## 2.3 更新题的落盘

- 同意全部 → publisher/queue 调 `Archive.apply_group`（等价 `python -m archive.cli apply-group --file <item.json>`），以 `group_id=queue_item_id` 幂等；失败会回滚整组。
- 拒绝全部 → 只写 `publisher/decisions.jsonl`，archive 不变。
- 评分「不在关注领域」→ 本 push 的更新题全部 dropped。

## 3. 知识库读写边界

| 动作 | 谁做 | 怎么做 |
|---|---|---|
| 读知识卡 / 命题 / 上下文 | agent | 任务文件已附带；需要更多时调 `archive.cli card-get / card-list / context / prop-list` |
| 写知识卡 / 命题 | **只有 archive** | `archive.cli apply-proposal` 或 `apply-decisions`（探索者审批后） |
| 提出更新 | agent（writer） | 写进 push 的 `proposals`，状态 `draft` |
| 让探索者同意 | publisher | 评分后发审批卡，落 `decisions.jsonl` |

agent **永远不直接编辑** `<运行时区>/archive/**` 下的 JSON；所有知识库写入都必须经过 archive CLI，保证可追溯、可回滚。

## 4. 为什么任务文件里不放提示词以外的状态

任务文件是**一次性的输入快照**：agent 失败可重跑同一条 `task` 命令；agent 的输出必须通过 `check` 才能进入下一步。这样 editor/writer 可以被任何 agent 实现，脚本仍然可测、可复现。

确定性启发式实现（`editor rank` / `writer write`）保留作为离线 fallback 和测试基准，但正式流程以 agent 任务模式为准。
