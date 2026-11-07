# 统一问题队列：状态机与闸门

> 实现范围：本文保留现有模块的字段、命令、校验和业务流程。产品定位与交互以[已确认决策](../docs/adr/0001-product-direction.md)为准；论文专用字段、飞书适配与现有确认门禁不构成新产品的全局限制。执行现有模块时仍遵守其校验，不据此绕过门禁。

实现契约：用于字段、状态机和兼容行为，不是本地任务规格。开发计划与验收以 GitHub Issues 为准；现行宿主调度与持久反馈见 `contracts/README.md` §12 和 `skills/frontier-run/SKILL.md`。现行常驻组件是用户手动启停的控制台（[ADR 0009](../docs/adr/0009-console-as-service.md)）；下文旧 listener / systemd 路径仅描述兼容实现，不授权启动旧部署。

- 票：[统一问题队列的状态机与闸门 #10](https://github.com/Moke110/frontier/issues/10)
- 状态：定稿（供 #16 / #17 / #18 实现）
- 术语：见 `GLOSSARY.md`；卡片/命题契约见 `contracts/README.md`

## 1. 目标与不变量

一次推送 = 一篇论文 = 1 个评分题 + 0–2 个更新题；命题综述题由第 10 条证据触发的独立题。所有题进入**同一个串行队列**，一次只问一题；队列清空后才允许启动下一次检索推送。

必须始终成立：

1. 全局最多 **1 个 active** 题；其余是 `pending`。
2. 同一个 push 内，**评分题永远排在更新题之前**。
3. 评分未答时，该 push 的更新题不得变 active。
4. 同一卡片或同一命题的多个提议**合并成一个题**。
5. 每个 push 最多 **2 个实体更新题**（知识卡片 + 命题合计）；超出的提议记录为 `overflow`，不问。
6. 终态（`answered / dropped`）不可变；`failed` 只能由人工 `retry` 回到 `pending`。
7. 知识库只在「同意全部」时写入；拒绝只落决定，不写 archive。
8. `queue/state.json` 的所有变更必须在 `queue/lock` 排他锁内完成。
9. 同一时刻最多一个「待推请求」在执行（start lease）。
10. `proposition_review` 题在综述 HTML 生成前是 `pending` + `payload.ready=false`：闸住检索，但不发给用户；生成后 `review-ready` 置为 ready 才可 active。

## 2. 文件与所有权

```
<运行时区>/
├── queue/
│   ├── state.json          # 队列唯一真相（queue 模块写）
│   ├── events.jsonl        # 只追加：enqueue/activate/send/answer/drop/fail/retry/request
│   ├── request.json        # 合并后的待推请求（queue 模块写）
│   ├── lock                # flock：所有 queue 状态变更
│   └── start.lock          # 检索启动租约（防两个 worker 同时开跑）
└── publisher/
    ├── messages.jsonl      # message_id → queue item（发送后追加）
    └── decisions.jsonl     # 同意/拒绝的原始决定
```

- **queue 模块是 `state.json` / `request.json` 的唯一写者**。publisher、trigger、定时器都通过 `queue.cli`（或等价函数）读写。
- `events.jsonl` 只追加，用于审计与重建；重建结果必须与 `state.json` 一致。
- `start.lock` 里存 `{owner, started_at, lease_until}`；**租约 = `max(10 分钟, agent timeout + 5 分钟)`**（默认 `1800s` → 35 分钟）。租约必须覆盖整趟检索/写作，否则兜底重试会在同一个 job 还在跑时抢跑；只有进程组真正死掉、租约过期后才允许接管。
- 兜底重试：`frontier-retry.timer` 每 2 分钟跑一次 `trigger.cli run-request --quiet`。没有 request / 租约有效 / job lock 被占 / 队列非空时立即返回，不写日志；租约过期且 job lock 空闲时接管。job lock 竞争**不得**释放别人的租约或累加 attempts。

## 3. 队列项 schema

`queue/state.json`：

```json
{
  "schema_version": 1,
  "active_id": "q_2026-10-21_card_hmbs_01_card_card_hmbs",
  "items": [
    {
      "id": "q_2026-10-21_card_hmbs_01_rating",
      "kind": "rating",
      "push_id": "2026-10-21_card_hmbs_01",
      "card_id": "card_hmbs",
      "status": "answered",
      "created_at": "2026-10-21T08:05:00+08:00",
      "sent_at": "2026-10-21T08:05:02+08:00",
      "answered_at": "2026-10-21T08:12:00+08:00",
      "message_id": "om_xxx",
      "attempts": 1,
      "answer": { "verdict": "great", "comment": "…", "operator_id": "ou_xxx" },
      "payload": {}
    },
    {
      "id": "q_2026-10-21_card_hmbs_01_card_card_hmbs",
      "kind": "card_update",
      "push_id": "2026-10-21_card_hmbs_01",
      "card_id": "card_hmbs",
      "status": "active",
      "created_at": "2026-10-21T08:05:00+08:00",
      "sent_at": "2026-10-21T08:13:00+08:00",
      "answered_at": null,
      "message_id": "om_yyy",
      "attempts": 1,
      "answer": null,
      "payload": {
        "target_type": "card",
        "target_id": "card_hmbs",
        "proposal_ids": ["p_…_1", "p_…_2"],
        "proposals": [
          {
            "proposal_id": "p_…_1",
            "kind": "add_alias",
            "diff": { "op": "add", "path": "ontology", "value": "PBGD" },
            "before": ["HMBS"],
            "after": ["HMBS", "PBGD"],
            "rationale": "…",
            "confidence": 0.7,
            "evidence": { "paper_id": "europepmc_…", "page": 3, "quote": "…" }
          }
        ]
      }
    }
  ]
}
```

字段规则：

| 字段 | 规则 |
|---|---|
| `id` | `q_<push_id>_rating` / `q_<push_id>_card_<card_id>` / `q_<push_id>_prop_<prop_id>` |
| `kind` | `rating` / `card_update` / `proposition_update` |
| `card_id` | 本次 push 的主卡片；所有题都带，便于按 push 丢弃 |
| `status` | `pending` / `active` / `answered` / `dropped` / `failed` |
| `payload` | 评分题为 `{}`；更新题含 `target_type / target_id / proposal_ids / proposals`；综述题另含 `stats / ready / html_path / base_url` |
| `answer` | 评分 `{verdict, comment, operator_id}`；更新 `{decision: agree_all\|reject_all, operator_id}` |
| `attempts` | 成功发出一次 +1；失败不增，重发 +1（上限 3） |

## 4. 分组、排序与限额

### 4.1 合并

writer 的同一批 proposals 按目标归组：

- `target_type=card` → 按 `target_id` 合并；
- `target_type=proposition` → 按 `target_id` 合并；
- `create_card` / `create_proposition` 没有既有目标：同一篇 push 的多个新建实体**各自成题**（每个新卡片/新命题一个题），与更新题共用 2 个实体名额。

一题内的 proposals 保持 writer 声明顺序；题内去重按 `proposal_id`。

### 4.2 限额

- 实体更新题：按 proposals 数组顺序全局取前 **2** 个目标（卡片/命题混排，新建实体同样占位）；其余记 `overflow` 事件（`reason=target_limit`），不建题。
- 评分题固定 1 个，不占限额；命题综述题独立，不占限额。
- 命题 counter 达到 10 且没有未完成的综述题时，`answer` 自动追加一个 `proposition_review` 题（`ready=false`）。同一命题同一时间最多一个未完成综述题。

### 4.3 队列顺序

入队顺序 = `[rating] + [更新题，按 proposals 顺序…]`。
题内顺序 = writer 声明顺序。队列**不做优先级插队**；`retry` 的题回到它原来的位置（见 §7）。

## 5. 状态机

```
                 enqueue push
                     │
                     ▼
   ┌──────────► pending ──activate──► active ──answer/agree/reject──► answered
   │                │                    │
   │                │                    ├── send fail (attempts<3) ──► pending (backoff)
   │                │                    ├── send fail (attempts=3) ──► failed
   │                │                    └── archive error ───────────► failed
   │                │
   │                └── rating=irrelevant（该 push 的更新题）──► dropped
   │
   └── manual retry（仅 failed）
```

| 当前 | 事件 | 结果 |
|---|---|---|
| — | `enqueue(push)` | 建评分题 + 更新题；若队列原本为空，评分题 → `active` 并发送 |
| `pending` | 前一项 `answered/dropped` | 若队列无 active 且无 failed，则本项 → `active` 并发送 |
| `active` | 评分回调 | `answered`；answer 落库；若 `irrelevant`，同 push 的 `pending/active` 更新题 → `dropped`（`reason=rating_irrelevant`，`answered_at=now`）；然后激活下一题 |
| `active` | 更新回调 `agree_all` | 先校验目标仍存在 → 调 archive 原子应用该题全部 proposals → `answered`；成功后激活下一题 |
| `active` | 更新回调 `reject_all` | 只写 `publisher/decisions.jsonl` → `answered`；不碰 archive |
| `active` | 发送失败 | `attempts<3`：回 `pending`，指数退避（1m/5m/15m），保持 active 空位；`attempts=3` → `failed` |
| `active` | archive 校验/写入失败 | `failed`（不部分写入）；后续题不激活 |
| `failed` | 人工 `retry` | 清空 answer/attempts → `pending`；若队列无 active，立即激活 |
| 任意非终态 | 重复/过期回调 | 忽略并返回 `ok`（幂等），不改状态 |
| 任意 | `irrelevant` 后收到更新回调 | 忽略；该题已是 `dropped` |

**关键规则：`failed` 会闸住整个队列。** 不自动跳过，不自动写入部分更新；必须人工 `retry` 或 `abandon`（`abandon` 把题标 `dropped`，`reason=manual_abandon`，然后继续）。

## 6. 回调幂等与路由

- 飞书卡片携带 `queue_item_id`；publisher 在 `messages.jsonl` 记 `{message_id, queue_item_id, push_id}`。
- 回调按 `message_id` → `queue_item_id` 路由；也接受卡片 value 里的 `queue_item_id`。
- 幂等键：`queue_item_id + action`。若题已终态：
  - 同 action → 返回 `{ok: true, deduped: true}`；
  - 不同 action（例如先同意后拒绝）→ 返回 `{ok: true, deduped: true, ignored: true}`，不改状态。
- 评分题只接受 `irrelevant/ok/great`；更新题只接受 `agree_all/reject_all`。未知 action 返回 `ok:false`。
- 非 owner 的回调拒绝（沿用现有 owner 校验）。

## 7. 失败与恢复矩阵

| 场景 | 处理 |
|---|---|
| 进程在 active 题发送后崩溃 | 重启扫描：`active` 且有 `message_id` → 重发同一题（新 message_id，`attempts+1`，卡片标「重发」）；不重复入队 |
| 进程在 active 题发送前崩溃 | `active` 且无 `message_id` → 直接重新发送 |
| 飞书发送失败 | 回 `pending` 并退避；3 次后 `failed`，闸住队列并告警 |
| 回调重复（飞书重试） | 幂等键命中，返回 ok |
| 回调乱序（更新题先到） | 更新题仍是 `pending`，返回 `ok:false, reason=not_active`；不推进 |
| 长时间未答 | 不跳过、不超时丢弃；24h 后发一次提醒（同题、同 message 语义），仍等用户 |
| archive 写入部分失败 | 该题 `failed`；archive 以 `group_id=queue_item_id` 做幂等，重试不会重复追加 history |
| queue/state.json 损坏 | 从 `events.jsonl` 重建；重建失败则拒绝启动并告警，不猜测 |
| 卡片或命题在同意前被删除 | 校验失败 → `failed`，提示人工处理；不自动降级为拒绝 |

## 8. 待推请求与闸门

`queue/request.json`：

```json
{
  "schema_version": 1,
  "requested_at": "2026-10-21T08:00:00+08:00",
  "sources": ["scheduled", "manual"],
  "card_id": "card_hmbs",
  "count": 2,
  "after_start": false,
  "started_at": null,
  "lease_until": null,
  "attempts": 0,
  "last_error": null
}
```

### 8.1 请求合并

- 手动「再推一篇」与每日 08:00 都调用 `queue.request_push(source, card_id?)`。
- 已存在 request → 合并：
  - `sources` 取并集；
  - `requested_at` 取更早者；
  - `count += 1`（仅审计）；
  - `card_id`：手动显式指定的优先；都没指定则 `null`（由检索默认规则选）。
- **不重复建 request**，因此不会因为手动+定时同时到达而推两篇。

### 8.2 原子启动

`queue.try_start()` 在 `queue/lock` + `start.lock` 内执行：

1. 若 `state.active_id` 非空，或存在任何非终态题 → 拒绝（`reason=queue_busy`）。
2. 若存在 `failed` 题 → 拒绝（`reason=queue_blocked`）。
3. 若无 request → 拒绝（`reason=no_request`）。
4. 若 `start.lock.lease_until > now` → 拒绝（`reason=starting`）。
5. 取租约（`max(10 分钟, agent timeout + 5 分钟)`），记录 `started_at`，返回 `{start: true, card_id, request_id}`。
6. worker 执行检索→写作→推送；新 push 入队时：
   - 若 request 的 `after_start=false` → 清除 request（本次请求已满足）；
   - 若 `after_start=true` → 保留一条新 request（`requested_at=now`，`sources` 沿用），等本轮队列清空后再启动。
7. 检索失败 → 释放租约，`attempts+1`，保留 request；由 `frontier-retry.timer` 每 2 分钟重试（不设次数上限，但每次失败都写 `request_failed` 并告警）。
8. **完成校验**：agent exit 0 不等于推送成功。worker 结束后比对 `archive/pushes` 与 `publisher/messages.jsonl`：
   - 没有新 push → `reason=no_push_output`，按失败处理；
   - 有新 push 但没有对应的 `push_file` 消息 → `reason=push_not_sent:<id>`（典型是 `duplicate_paper` 被拒），按失败处理并告警。
   两种失败都释放租约、保留 request，交给兜底重试。

### 8.3 触发点

- 评分/更新回调处理完后，若 `queue` 已空且没有 `failed`，自动调用 `try_start()`。
- 手动监听器：先 `request_push("manual", card?)`，再 `try_start()`；若 `queue_busy` 回复「已排队，答完当前问题自动推送」。
- 每日 08:00：`request_push("scheduled")` + `try_start()`，同一套合并与闸门。
- 兜底重试：`frontier-retry.timer` 每 2 分钟 `run-request --quiet`；只在「有 request + 租约过期 + job lock 空闲 + 队列空」时真正启动。
- 检索启动与队列写入使用同一把 `queue/lock` 的短临界区；实际检索在锁外执行。

## 9. 边界用例表

| # | 输入/事件 | 期望 |
|---|---|---|
| 1 | push 有 0 条 proposals | 只建评分题；答完队列即空，自动启动待推请求（若有） |
| 2 | 同一卡片 3 条 proposals | 合并为 1 题，题内 3 条 diff；仍只占 1 个实体名额 |
| 3 | 5 张不同卡片各 1 条 | 只建前 2 个实体题；其余 3 条写 `overflow` 事件，不发问 |
| 4 | 评分 `irrelevant` | 该 push 所有更新题 → `dropped`；archive 不写；下一题/下一次推送可继续 |
| 5 | 评分 `irrelevant` 后飞书又回调更新题 | 幂等忽略；返回 ok；不写 archive |
| 6 | 更新题回调先于评分 | 返回 `ok:false, not_active`；评分题仍 active |
| 7 | 同意全部但目标命题已被删 | 题 → `failed`；不写任何部分更新；队列闸住 |
| 8 | 同意全部时 archive 写到一半崩溃 | 该题 `failed`；用同一 `group_id` 重试不会重复写 |
| 8.1 | 第 10 条证据被采纳 | 追加 `proposition_review` 题（ready=false）；队列非空，检索不启动 |
| 8.2 | 综述题 ready=false 且无其他题 | 不发送任何卡；等待 review-request 生成；队列仍算未清空 |
| 8.3 | 综述 `keep_previous` | 只落决定；review 不变；counter 保留；下一条证据采纳时重新生成 |
| 8.4 | 综述 `adopt` | 写入新 review；counter 归零；题 `answered` |
| 9 | 同一按钮回调两次 | 第二次 `deduped:true`，状态不变 |
| 10 | 先同意后拒绝 | 拒绝被忽略；状态仍 `answered/agree_all` |
| 11 | active 题发送后进程崩溃 | 重启重发（attempts+1）；不重复入队、不跳过 |
| 12 | 发送连续失败 3 次 | 题 `failed`；队列停止；告警；人工 retry/abandon |
| 13 | 队列非空时手动「再推一篇」 | 只写 request；不检索；回复已排队 |
| 14 | 08:00 与手动同时到达 | 合并成 1 个 request（sources 并集），只推 1 篇 |
| 15 | 检索进行中又来手动请求 | 记 `after_start=true`；当前推送完成后，新一轮队列清空再启动 |
| 16 | 检索完成但没有新论文 | 清 request；不建队列；回复「今天没有新的」 |
| 17 | 检索 worker 崩溃，租约过期 | 兜底 timer 接管；request 保留，attempts+1 |
| 18 | 用户 24h 未答题 | 发一次提醒；不跳过、不超时丢弃 |
| 19 | 队列为空且无 request | idle；不产生任何发送 |
| 20 | `state.json` 与 events 不一致 | 以 events 重建；重建失败拒绝服务并告警 |
| 21 | worker 还在跑（租约未过期），兜底 timer 触发 | 直接返回 `busy/starting`；不改 request、不累加 attempts |
| 22 | agent 超时 | 杀整个进程组；`timeout` 日志带 stdout 尾部；Lark 告警；保留 request |
| 23 | agent 选中已推送论文，`publisher send` 返回 `duplicate_paper` | CLI exit 1；agent 立即终止；worker 判 `push_not_sent`；告警＋重试 |
| 24 | agent exit 0 但既没新 push 也没说「今天没有新的」 | 判 `no_push_output`；告警＋重试，不静默 |
| 25 | 拉起子进程失败 / 立即非零退出 | 写 `trigger-spawn.log`，`spawn_failed` 事件，Lark 告警；租约照常过期后重试 |

## 10. 对下游实现的约束

- **#16 统一问题队列与逐题卡片实现**：按 §2–§7 实现 `queue` 存储、锁、`queue.cli`（`enqueue / next / answer / retry / abandon / request-push / try-start / status`）；卡片必须带 `queue_item_id`；archive 增加按 `group_id` 幂等的 `apply-group`。
- **#17 writer 提案实现与更新题接入队列**：writer 输出按 §4 分组；publisher 在推送后调用 `enqueue`，不得直接发更新题。
- **#18 每日 08:00 定时检索与队列闸门**：定时器只调用 `request_push("scheduled")` + `try_start()`，不自己判断队列；手动监听器同样。
- **#19 端到端验收**：至少覆盖边界用例 1、4、9、13、14、15、18。

## 11. 现有队列实现限制

- 不做优先级/插队、批量答题、自动跳过、自动审批。
- 不做多用户/多人同时答题；owner 单人。
- 评分不反哺排序或权重（本阶段）。

## 12. 实现状态

- 代码：`question_queue/`（`store.py` / `cli.py`）；publisher 接入见 `publisher/service.py`；
  archive 原子应用见 `Archive.apply_group`。
- 飞书 Card 2.0 的 callback 按钮把数据放在 `action_value`（JSON 字符串），不是 `action_name`；
  publisher 两处都解析，`message_id` 仍用于路由。
- 2026-10-21 真实验证：评分卡 → 更新卡（`reject_all`）→ 队列清空；拒绝未写 archive。
  测试数据已从 `~/knowledge-loop` 清理。
- 每日 08:00：`frontier-daily.timer` → `trigger.cli scheduled`；队列非空只写 `request.json`，
  队列清空后评分回调拉起 `trigger.cli run-request`。定时器不要加 `Requires=frontier-daily.service`
  （启动 timer 会立刻拉起 service），`Persistent=false` 避免安装/重启时补跑。

