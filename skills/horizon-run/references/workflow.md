# 内置 Agent 工具映射

> 实现范围：本文保留现有模块的字段、命令、校验和业务流程。产品定位与交互以[已确认决策](../../../docs/adr/0001-product-direction.md)为准；论文专用字段、飞书适配与现有确认门禁不构成新产品的全局限制。执行现有模块时仍遵守其校验，不据此绕过门禁。

此工作流中的 `python -m MODULE ...` 使用 `horizon_cli` 工具调用：`module` 为 MODULE，
`args` 为后面的参数列表，不经过 shell。所有中间任务和 outbox 路径使用运行时目录
`.horizon/` 内的绝对路径；不得向程序源码目录写入产物。读取和写入 JSON 使用
`read_file`、`write_file`。不要读取开发 AGENTS.md 或模型凭据。

# horizon 检索、写作与知识更新参考

由 [horizon 运行时入口](../SKILL.md) 按需加载的完整流程参考；命令以**代码目录**为工作目录执行。agent 负责**排序（editor）与写作（writer）**；脚本负责取数、校验、入队、落盘。所有知识库写入都经过 archive / `KnowledgeStore`，agent 不直接改存储。

**命令入口随安装方式变化**：发行包安装用 `<code>\bin\horizon.cmd` 执行 `hostrun.cli` 子命令（它调用内置运行时 `<code>\runtime\python.exe`），其他模块命令用 `<code>\runtime\python.exe -m <module>`；源码安装用安装记录里的私有解释器执行 `python -m hostrun.cli ...`。下文示例写作 `python -m hostrun.cli` 的地方，发行包安装替换为 `<code>\bin\horizon.cmd --code <code> --archive <archive>`（全局参数在子命令前），其他 `python -m <module>` 示例替换为 `<code>\runtime\python.exe -m <module>`。两种安装都不依赖系统 `python` 或 PATH 上的解释器。

文中的 Phase 标签表示能力来源，不能作为当前安装或云端切换已通过的证据。运行前检查安装报告与实际知识库状态；同步由控制台每 10 分钟的有界轮询承担，不单独安装系统定时器。真实验收限制见 [验收记录](../../../docs/acceptance/)。

## 1. 前置条件、目录与运行前提

1. 已按 [安装参考](installation.md) 验证本地宿主、安装方式（发行包或源码）、飞书 CLI 和知识库；定时运行由控制台计时，不再依赖宿主内置调度。
2. `lark-cli` 装在代码目录的运行目录并已登录；登录状态由 `hostrun install` 的 `identity` 步骤验证，不靠 `command -v` 猜。
3. 常驻的只有探索者可见、关闭即停的控制台（[ADR 0009](../../../docs/adr/0009-console-as-service.md)）：它按每日时刻启动一次 agent 运行、消费飞书消息与卡片回调、每 5 分钟反馈轮询、每 10 分钟 `knowledge.cli pull`。agent 运行是「一次有界运行 → 退出」，不挂会话。不装 systemd、不写持久环境变量。
4. 目录拆分：代码、运行时或私有环境、配置、运行状态、日志与资料登记在**代码目录**（`<code>`，运行时区 `<code>/.horizon/`）。发行包安装的代码目录是解压后的发行包，自带 `<code>\runtime\` 与 `<code>\bin\horizon.cmd`，没有 `.venv` 与 uv；源码安装的代码目录是 git checkout，私有环境在 `<code>/.horizon/.venv`。原文 `originals/` 与推送件 `pushes/` 在**资料目录**（`<archive>`），资料目录里没有台账。

所有模块命令显式传入 `--code <代码目录>` 与 `--archive <资料目录>`（旧 `--home` 已移除；安装锁与安装记录里保存两者的绝对路径）：

```powershell
python -m hostrun.cli --code <code> --archive <archive> install status
python -m hostrun.cli --code <code> --archive <archive> status
```

```powershell
# 通过安装检查验证身份，只报告就绪状态，不打印原始 auth 输出
<code>\runtime\python.exe -c "import yaml"
```

## 2. 领域模型

### 知识卡片

`id / label / ontology / annotation`，外加可选布尔 `under_review`。卡片不存任何链接；连接只存在于命题的 `related_knowledge`。

- `ontology` 驱动召回；`annotation` 驱动精确性（定义、排除项、易混项）。
- id 形如 `card_hmbs`；语义 slug，冲突追加 `_2`。
- `under_review: true` 表示探索者保留但暂不启用：不参与检索计划、排序与写作上下文；仍同步 Base、仍计数、仍占配额。对应探索者在多维表格里取消「待复核」勾选后经 pull 清除。

### 命题

`id / title / review / related_knowledge / evidence / changelog / evidence_updates_since_review`，外加可选布尔 `under_review`。

- `related_knowledge` 至少 2 张已存在卡片。
- **新建命题必须带 ≥2 条 evidence**；没有证据的命题不入库。
- `review` 是综述；`evidence` 是证据条目；`changelog` 只记 title / review / related_knowledge 的变更。
- `under_review` 的命题不进写作上下文；它关联的正常卡片照常使用。
- 旧 `history[].evidence` 在迁移时拆成 evidence 条目，标 `migrated_from_v2`。

### 证据条目

```json
{
  "id": "ev_virtual_cell_perturbation_01",
  "stance": "supports | contradicts",
  "conclusion": "≤200 字，只概括方向一致的报道结论",
  "papers": [
    {
      "paper_id": "arxiv_2610.25021v1",
      "push_id": "2026-10-20_card_virtual_cell_02",
      "page": 4,
      "quote": "原文引文，8–500 字",
      "added_at": "2026-10-20T09:00:00+08:00"
    }
  ],
  "created_at": "…",
  "updated_at": "…"
}
```

- 一条 evidence 内**只放方向一致的报道**；同一命题下可以存在互相矛盾的 evidence。
- `stance` 相对命题标题：`supports` = 支持，`contradicts` = 反证。
- 新报道与某条 evidence 同立场且结论同向 → 并入该条（追加 `papers[]`、可改写 `conclusion`、更新 `updated_at`）；异向 → 新建一条。

### 综述 review

- ≤2000 字，固定四节：**结论 / 支持证据 / 反证与边界 / 待验证**；每节 1–3 段。
- 基于当前全部 evidence，不引入 evidence 之外的新事实。
- 每采纳 10 条证据更新自动重写一次；被拒后保留计数，下一条证据被采纳时重试。

### 变更日志 changelog

只记非证据变更：`at / action / field / before / after / rationale / source`。review 的上一版从 changelog 取，用于生成 diff。`under_review` 的切换不写 changelog。

## 3. 主循环

只有运行时入口的 daily 闸门允许后才开始以下正向链路。使用当前安装的入口（发行包为 `<code>\bin\horizon.cmd` 与 `<code>\runtime\python.exe`，源码安装为记录的私有解释器），日期由控制台按探索者时区生成，所有模块命令显式传 `--code`/`--archive`；下列示例省略该重复参数。先处理已提交反馈，未答问题挡住新推送。

### 3.1 检索计划

```bash
python -m horizon.cli validate
python -m horizon.cli plan --date <YYYY-MM-DD> --days 7 --out plan.json
```

每张卡片必须有 `id / label / ontology / annotation`；`under_review` 的卡片与命题在计划与写作上下文中被过滤。

### 3.2 取原文并入档

```bash
python -m retriever.cli fetch --plan plan.json --out candidates.jsonl --source live
# 离线：--source fixture --fixtures fixtures/papers
```

逐条 `python -m archive.cli add-paper --file <json>`；`add-paper` 按 `source:source_id` 去重。

### 3.3 排序（agent 亲自做）

```bash
python -m editor.cli task --candidates candidates.jsonl --date <YYYY-MM-DD> \
  --limit 1 --per-checkpoint 1 --out editor-task.json
```

读 `editor-task.json`，按 `instructions` 写 `selection.json`：

- 只用 candidates 里有的论文；
- 每条给 `checkpoint_id` / `paper.paper_id` / `score`(0–1) / `rationale`；
- 不超过 `limits`。

```bash
python -m editor.cli check --task editor-task.json --selection selection.json
```

不通过就修正，**不要绕过**。

### 3.4 写作与推送（agent 亲自写）

```bash
python -m writer.cli task --selection selection.json --out writer-task.json
python -m writer.cli figures --selection selection.json --out outbox
```

为每个 item 写 `outbox/pushes/<push_id>.json`：

- 七段：`takeaway / background / methods / results / why / limits / sources`；
- `body_html` 是正文片段，图片用 `<figure data-asset="fig-1"></figure>` 占位；
- `assets` 用 `figures.json` 的结果；原文图标 `paper`，生成图标 `generated`；
- `why` 必须引用 `context`（卡片摘要、命题、归档篇数至少其一）；
- proposals 一律 `status: draft`，绝不直接写知识库。

Phase 3 起渲染为**自包含 HTML**（不再是 PDF）：

```bash
python -m writer.cli check --task writer-task.json --outbox outbox
python -m writer.cli render --push outbox/pushes/<push_id>.json --out outbox/<push_id>.html
python -m archive.cli add-push --file outbox/pushes/<push_id>.json
python -m library.cli ensure-source --paper <push 里的 paper_id>
```

`ensure-source` 把原文 PDF 传进云盘「原文」库并在 Base 登记；付费墙拿不到 PDF 时记 `无原件`，不阻断推送。推送件在 `publisher send` 成功后自动入库。

### 3.5 入队与发送

```bash
python -m publisher.cli send --push outbox/pushes/<push_id>.json --html outbox/<push_id>.html --to ou_xxx
python -m hostrun.cli --code <code> --archive <archive> feedback sync
```

- `send` 是**对外可见**操作：遵循安装时确认的收件人、文件与条数范围；超出原授权时再确认。
- 只发送当前 active 题；一题答完才发下一题。
- 无飞书环境：`--dry-run`（send）与 `--offline`（ingest）。
- 查看队列：`python -m question_queue.cli status`。

### 3.6 评分

- `irrelevant / ok / great`；本阶段评分只用于闸门，不反哺排序。
- `irrelevant` → 该 push 的更新题全部 `dropped`。

### 3.7 更新题（卡片 / 证据）

- 同意 → 立即写入（Phase 3：WAL + Bitable）；拒绝 → 只写 `publisher/decisions.jsonl`。
- 同一目标的多个 proposals 合并成一题；每篇推送最多 **2 个实体目标**。
- 证据题只处理一个命题；卡片题只处理一个卡片。

### 3.8 综述题

- 第 10 条证据被采纳 → 队列追加 `proposition_review` 题（`ready=false`），占住闸门。
- 控制台在派生的一次 Agent 运行中处理该题：`writer.cli review-task` → 写 review JSON → `review-check` → `review-render` →
  `question_queue.cli review-ready` → `publisher.cli deliver`。Agent 运行的单飞行、租约、时限与不重试护栏沿用运行时入口。
- 综述 HTML + 确认卡推送；`采纳此版` 写入并清零计数，`保持上一版` 保留计数。
- 综述题不占每篇推送的 2 个实体名额。

### 3.9 队列清空后

- 队列清空且无 failed → 执行待推请求（飞书「再推一篇」或控制台的每日时刻）。
- 有 failed 时队列闸住，用 `question_queue.cli retry/abandon` 处理。

## 4. 更新契约

### 4.1 名额

- 每篇推送最多 **2 个实体目标**，知识卡片 + 命题合计，**新建也占名额**。
- 同一目标的多个操作合并成一题，不重复占名额。
- 超额时按 `proposals` 数组顺序全局取前 2；`writer check` 直接拒绝 >2。
- 综述题独立，不占名额。

### 4.2 操作表

| `op` | 目标 | `diff.path` / `value` |
|---|---|---|
| `add_card_ontology` | 已有卡片 | `ontology` / 新词或词列表 |
| `update_card_annotation` | 已有卡片 | `annotation` / 新文本 |
| `create_card` | 无 | `card` / `{id?, label, ontology, annotation}` |
| `create_proposition` | 无 | `proposition` / `{id?, title, review, related_knowledge, evidence[≥2]}` |
| `update_proposition_title` | 已有命题 | `title` / 新标题 |
| `update_proposition_related_knowledge` | 已有命题 | `related_knowledge` / ≥2 卡 id |
| `add_proposition_evidence` | 已有命题 | `evidence` / 新条目 `{conclusion, stance, papers[]}` |
| `merge_proposition_evidence` | 已有命题 | `evidence` / `{target_evidence_id, papers[], conclusion?}` |
| `update_proposition_review` | 已有命题 | `review` / 新综述（仅综述题使用） |

硬规则：

1. `before` 必须等于写入时的当前值（防陈旧 diff）；`after` 必须等于 `diff.value` 或按操作表推导。
2. `evidence.paper_id` 必须是本次 `push.paper_ids` 之一；`page` 为 ≥1 整数或 `null`（摘要）；`quote` 8–500 字且可在原文定位。
3. `create_proposition.related_knowledge` ≥2 张已存在卡片；本次新建的卡片不能立刻被新命题引用。
4. `create_proposition` 必须带 ≥2 条 evidence；每条 evidence 的 `conclusion` ≤200 字。
5. `proposal_id` 形如 `p_<push_id>_<NN>`；`status` 只能 `draft`。
6. writer 永不直接写 archive / Bitable。

### 4.3 证据规则

- 每条 evidence 只放**方向一致**的报道；反证单独成条。
- 同立场且结论同向 → `merge_proposition_evidence`；否则 `add_proposition_evidence`。
- 一条 evidence 至少对应一篇报道；同一结论可以归并多篇。
- 计数：每采纳一条 add/merge 提议 +1（不是按报道篇数）；满 10 触发综述。

### 4.4 综述规则

- 四节固定，≤2000 字；只基于 evidence。
- diff 按段落/句子对齐，内联红/绿，颜色降饱和（背景 `#fdecec` / `#e8f5e9`，文字保持正文色）。
- 证据按 `supports` / `contradicts` 分组；引文放在 `<details>` 里折叠。
- 被拒后保留计数，下一条证据被采纳时重新生成，不立即连问。

## 5. 三类飞书卡

顶栏标题 = 类型 + 进度，**无副标题**：

- `知识卡片更新 · 第 N / M 题`
- `命题证据更新 · 第 N / M 题`
- `命题综述更新 · 第 N / M 题`

正文第一行必须是实体 id/title：

- 卡片：`**`card_hmbs`** · HMBS`；
- 命题：`**`prop_xxx`** · 标题`；
- 新建：`**新建知识卡片** · label` / `**新建命题** · title`。

卡片更新正文：来源论文 → 变更项（ontology / annotation 行级 diff）→ 依据 → 理由 + 置信度。
证据更新正文：来源论文 → 立场徽标 → 新结论 → 报道/页码/引文 → 归并目标。
综述更新正文：证据计数（支持 X / 反证 Y）→ diff 行数 → Base 记录链接。

按钮：

- 卡片/证据：`同意全部`（左，蓝）/ `拒绝全部`（右，红）；
- 综述：`采纳此版`（左，蓝）/ `保持上一版`（右，红）。

页脚统一说明：同意后立即写入；拒绝保持原样；答完才发下一题。评分与采纳按钮由控制台的事件面直接接收，不经过意图判定与确认卡片。

## 6. 落盘

**Phase 3a（当前，双读期）**：

- 本地 `archive/` 仍是读与写的权威；Bitable 是镜像。
- 每次同意更新后，spawn `python -m knowledge.cli sync`：按业务 id 幂等 upsert 卡片 / 命题 / 证据 / 报道 / 变更日志，并重建关联。
- 控制台**每 10 分钟**执行：`knowledge.cli pull`（把 Bitable 文本列的手工编辑与「待复核」勾选拉回本地）→ `sync` → `compare`（只记差异，不让轮询失败）。不再有 `horizon-sync.timer`。
- 首次 `pull` 只建立 `knowledge/state.json` 基线；之后只接受 title / review / annotation / conclusion 的文本改动与 `under_review` 的布尔改动。
- 非法编辑不落本地：在 Base 的「待修正」列写原因。

**Bitable 适配路径的读写协议**：

- 在 Bitable 适配路径中，本地 JSON 用作只读快照；此协议不指定 Horizon 自建知识库的存储方案。
- 读：`KnowledgeStore` 实时读 Bitable；测试与 `--offline` 用 `FixtureStore`。
- 写：本地 WAL（pending + proposals + before 快照）→ 逐条写 Bitable；按 `group_id` 幂等，失败断点续跑。
- 写前校验：id 唯一、≥2 关联卡、conclusion ≤200、review ≤2000、quote 可定位、paper_id ∈ push、before 不陈旧。

## 7. 手工编辑与同步（Phase 3）

- 只动文本列：`title / review / conclusion / annotation` 与「待复核」勾选框；结构列（id、关联、日期、计数）只读。
- 编辑视图隐藏结构列；这是约定，不是强制权限。
- 控制台的 10 分钟 pull 扫描 Bitable 与本地「上次已见」台账：文本变化补一条 changelog，`under_review` 变化只更新记录；校验失败写「待修正」，不静默接受。取消「待复核」后最多 10 分钟生效，不需要重启控制台。

## 8. 探索者偏好与硬规则

- 中文、克制、具体；不写空话套话。
- **不编造**：论文只来自 candidates；证据、别名、命题必须可追溯。
- 知识库只写提议；同意后才落盘。
- 先 `check` 再入库；校验失败就修，不改校验器。
- 发送遵循已确认的收件人、文件与条数范围；预览使用 `--dry-run`。
- 结论 ≤200 字、综述 ≤2000 字是硬上限。
- HTML 排版：正文 `clamp(16px, 1.1vw + 12px, 20px)`、行高 1.85、最大宽 40em、系统字体、跟随深色模式；图片 100% 宽；表格可横向滚动。

## 9. 故障排查

| 现象 | 处理 |
|---|---|
| 评分未推进 | 检查持久反馈是否提交，再看控制台是否在前台运行（`hostrun.cli console control status`）；控制台不在时启动它，或等下一次有界运行 |
| 飞书命令没反应 | 控制台窗口关闭时不消费任何消息；重新启动控制台后已提交反馈会继续处理 |
| 发卡报 `missing scope` | 按 `missing_scope` 重新授权 |
| 卡片只显示 header | 客户端 < 7.20，升级飞书 |
| `lark-cli --file` 报 `unsafe file path` | 只传当前工作目录下的相对路径 |
| 队列闸住 | `question_queue.cli status`，再 `retry` / `abandon` |
| Bitable 写入部分成功（Phase 3） | 查 group journal，`retry` 从断点继续，不重复写 |
| 取消「待复核」后检索仍不包含该卡片 | 等下一次 10 分钟 pull，或让控制台立即执行一次 `knowledge.cli pull` |
| agent 运行没有产出 | 看 `/horizon-logs` 与 `<code>/.horizon/logs/agent-<run_id>.log`；确认内置 Pi 运行时与模型配置可用 |

## 10. 参考

- `contracts/README.md`：模块文件契约。
- `contracts/agent-tasks.md`：editor / writer 任务包与校验规则。
- `contracts/question-queue.md`：队列状态机与闸门。
- `docs/adr/0001-product-direction.md`：已确认的产品方向。
- `fixtures/`：离线运行时区与论文。
