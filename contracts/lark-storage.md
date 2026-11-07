# 飞书存储适配：原文库 / 推送库 / 配额

> 实现范围：本文保留现有模块的字段、命令、校验和业务流程。产品定位与交互以[已确认决策](../docs/adr/0001-product-direction.md)为准；论文专用字段、飞书适配与现有确认门禁不构成新产品的全局限制。执行现有模块时仍遵守其校验，不据此绕过门禁。

实现契约：用于字段、状态机和兼容行为，不是本地任务规格。开发计划与验收以 GitHub Issues 为准；现行宿主调度与持久反馈见 `contracts/README.md` §12 和 `skills/frontier-run/SKILL.md`。现行常驻组件是用户手动启停的控制台（[ADR 0009](../docs/adr/0009-console-as-service.md)）；下文旧 listener / systemd 路径仅描述兼容实现，不授权启动旧部署。

- 范围：既有 `library` 飞书适配的字段、状态机与兼容行为；不规定产品使用飞书作为唯一存储。
- 术语：见 `GLOSSARY.md`（Installation and storage）；知识五表契约见 `contracts/README.md` §10.5

## 1. 所有权

| 资源 | 适配链路写入模块 | 适配链路读取来源 |
|---|---|---|
| 知识五表（卡片 / 命题 / 证据 / 报道 / 变更日志） | `knowledge` | Base |
| 原文 PDF + 「原文」表 | `library` | 云盘 + Base |
| 推送 HTML + 「推送日志」表 | `library` | 云盘 + Base |
| 「决定」表 | `library` | Base |
| 「配置」表 | 用户（`library` 只读） | Base |
| 本地缓存 / `raw/*.json` 物化视图 | `archive` | 否，可重建 |
| 对外消息（含配额提醒） | `publisher` | —— |

`library` 不直接发消息：它只写 `<运行时区>/library/alerts/<ts>.json`，由 `publisher` 的定时器读取后发送。

## 2. 云盘布局

```
飞书云盘/frontier/原文/<paper_id>.pdf              # ≤ 19 MB
飞书云盘/frontier/原文/<paper_id>.pdf.part01       # 超出单文件上限时按序分片
飞书云盘/frontier/推送/<push_id>.html
```

- 扁平、只用业务 ID 命名。不做年月分目录：移动文件可能换 token，Base 链接会失效。
- **租户单文件上限**：当前租户的 Drive API 对超过 20 MB 的文件返回 `1061043 file size beyond limit`。因此 >19 MB 的 PDF 切成 19 MB 分片，顺序与 token 记在「原文」的 `分片` 列；读取时拼接后按 md5 校验。
- 文件名即主键，不含标题 —— 标题会改，ID 不会。
- 两个目录的 token 存「配置」表；本地不写死。

## 3. Base 表

### 3.1 「原文」

| 字段 | 类型 | 说明 |
|---|---|---|
| 原文 ID | 文本 | `paper_id`，唯一；幂等键 |
| 标题 | 文本 | |
| 作者 | 文本 | `; ` 连接 |
| 期刊 | 文本 | |
| 发表年份 | 数字 | |
| DOI / PMID / PMCID / arXiv | 文本 | 取不到留空 |
| 摘要 | 长文本 | |
| 分类 | 文本 | 来自 manifest / 主题 |
| 是否预印本 | 复选框 | |
| PDF 页数 | 数字 | |
| 来源 | 单选 | `pdf` / `arxiv` / `europepmc` / `fixture` |
| 原文来源链接 | URL | 可重下的地址（DOI / PMC / arXiv abs） |
| 入库日期 | 日期 | 首次在库时间 |
| 云盘链接 | URL | 空 = 无原件或已离线归档 |
| 文件大小 | 数字 | 字节；配额只算这个 |
| md5 | 文本 | 拼接后的完整文件 md5；缓存命中与备份校验的依据 |
| 分片 | 文本 | JSON 数组 `[{i,name,token,url,size}]`；空 = 单文件 |
| 状态 | 单选 | 待上传 / 在库 / 无原件 / 已离线归档 |
| 备份位置 | 文本 | 本地绝对路径，仅已离线归档时填 |
| 报道 | 关联 | →「报道」，反向 |

### 3.2 「推送日志」

| 字段 | 类型 | 说明 |
|---|---|---|
| 推送 ID | 文本 | `push_id`，唯一；幂等键 |
| 日期 | 日期 | |
| 知识卡 | 关联 | →「卡片」 |
| 原文 | 关联 | →「原文」，一篇推送一篇论文 |
| 标题 | 文本 | |
| 一句话结论 / 背景 / 方法 / 结果 / 与知识的关系 / 局限 / 来源 | 长文本 ×7 | 对应 `sections` 的 7 个键 |
| 评分 | 单选 | irrelevant / ok / great |
| 评语 | 长文本 | 用户可空 |
| 云盘链接 | URL | 推送库文件 |
| 文件大小 | 数字 | |
| md5 | 文本 | 幂等键的第二段；内容变了就是冲突 |
| 入库日期 | 日期 | 发送成功的时间 |
| 状态 | 单选 | 在库 / 已离线归档 / 回填 |

七段列名与 `sections` 键的映射固定为：
`takeaway / background / methods / results / why / limits / sources`。

### 3.3 「决定」

| 字段 | 类型 | 说明 |
|---|---|---|
| 决定 ID | 文本 | `dec_<问题 ID>_<NN>`，唯一 |
| 问题 ID | 文本 | 同一次问答的分组键 |
| 时间 | 日期 | |
| 推送 | 关联 | →「推送日志」 |
| 提议 ID | 文本 | `proposal_id` |
| 操作 | 单选 | 采纳 / 拒绝 |
| 目标 | 文本 | 卡片或命题 id |
| 评语 | 长文本 | 用户可空 |

一行 = 一条提议。一次 `agree_all` / `reject_all` 产生 N 行，共享 `问题 ID`。

### 3.4 「配置」

单行表（`config`）：

| 字段 | 类型 | 默认 |
|---|---|---|
| 容量上限（GB） | 数字 | 5 |
| 提醒阈值（%） | 数字 | 80 |
| 原文目录 token | 文本 | |
| 推送目录 token | 文本 | |
| 最近对账 | 日期 | |

用户直接改数值即生效；`library` 每次预检读一次，本地缓存 10 分钟。

## 4. 入库流程

### 4.1 原文（`library.cli put-source`）

1. 读「原文」行。`状态=在库` 且 md5 相同 → `skipped`。
2. 预设幂等键 `(原文, paper_id, md5)`；同名文件已存在且 md5 相同 → 直接补行。
3. 上传 PDF → 取回 `file_token` 与永久链接。
4. 写/更新 Base 行：`状态=在库`、`云盘链接`、`文件大小`、`md5`、`入库日期`（首次）。
5. 预检配额：超限则**不上传新原文**，写 alert，返回 `quota_blocked`（退出码 3）。

`状态=无原件` 的行不进上传流程，只登记书目与来源链接。

### 4.2 推送（`library.cli put-push`）

由 `publisher` 在**消息发送成功之后**调用，传 `push_id` 与本地 HTML 路径。

1. 渲染：`writer render --format html` 产出单文件自包含 HTML（图片 base64）。
2. 上传 → 写「推送日志」行（七段、评分留空、`状态=在库`）。
3. 幂等：同 `push_id` 且文件大小/md5 相同 → `skipped`；不同 → 保留旧文件、报 `conflict`（见 §8）。
4. 推送件**不受配额拦截**，超限只写 alert。

发送失败 → 不调用 `put-push`，文件留在本地 `outbox/`。

## 5. 读路径

`library.resolve(paper_id) -> Path`：

1. `<运行时区>/cache/raw/<paper_id>.pdf` 存在且 md5 与 Base 一致 → 命中。
2. 否则从云盘下载（有 `分片` 则按 `i` 顺序拼接），校验 md5；不一致 → 删除重下，二次失败报 `corrupt`。
3. `状态=已离线归档` → 从 `备份位置` 复制并校验 md5；备份也不在 → 报 `missing`，提示用户重下或恢复。
4. `状态=无原件` → 报 `no_source`，调用方退化为摘要锚定。

派生缓存（`cache/text/`、`cache/pages/`）按需生成；`library.cli cache prune --max-size 2G` 按最近使用清理。
缓存不计入配额。

## 6. 配额

- 用量 = Σ `文件大小`，范围是「原文」+「推送日志」中 `状态=在库` 的行。
- 检查时机：每次 `put-*` 前预检（只读 Base，不扫目录）；每日一次全量对账顺带校正。
- 阈值：`用量 / 上限 ≥ 80%` → 提醒一次；`≥ 100%` → 每次尝试都提醒，且拒绝新原文。
- 冷却：同一状态 24 小时内最多一条；跨阈值（80%→100%）立即发；**100% 时每次入库尝试都发**（这是唯一会持续打扰你的状态）。
- alert 文件：

```json
{
  "schema_version": 1,
  "created_at": "2026-10-26T09:00:00+08:00",
  "level": "warn | full",
  "used_bytes": 1500000000,
  "limit_bytes": 5000000000,
  "percent": 30,
  "candidates": {"count": 12, "bytes": 800000000},
  "instruction": "清理 frontier 原文库：……"
}
```

- 指令段落自带数字（候选篇数、字节数、备份目录），用户复制即用：

> 清理 frontier 原文库：把 Base「原文」表中 `状态=在库`、未被「报道」引用的 N 篇（约 X MB）下载到 `~/frontier-offline/2026-10-26/`，逐篇校验 md5 一致后，把状态改为 `已离线归档` 并填写备份位置，再从飞书云盘删除文件；完成后报告释放空间与剩余用量。

## 7. 清理与恢复

清理（`library.cli evict`，**永不自动执行**）：

1. 候选排序：未被报道引用 → 被引用（入库最老优先）。推送件不参与自动清理（它们才是推送库的本体）。
2. 下载到 `~/frontier-offline/<YYYY-MM-DD>/`，校验 md5。
3. Base 改 `状态=已离线归档` + `备份位置`。
4. 删云盘文件。
5. 备份失败任何一步 → 中止，不删。

恢复（`library.cli restore --paper <id>`）：从 `备份位置` 校验 md5 → 上传 → `状态=在库` + `云盘链接` → 清 `备份位置`。

## 8. 失败矩阵

| 场景 | 结果 | 修复 |
|---|---|---|
| 上传成功、Base 写失败 | 孤儿文件 | 下次同幂等键重跑补行；每日对账兜底 |
| 上传失败 | 无 Base 行 | 重试；本地文件与草稿仍在 outbox |
| Base 行在、文件不在 | 断链 | 对账改 `待上传` 并提醒；若 `备份位置` 非空则置 `已离线归档` |
| 删文件成功、Base 改状态失败 | 在库行 + 无文件 | 对账时若 `备份位置` 非空 → 直接置 `已离线归档`（不得改回待上传，否则会重传） |
| Base 有行、`备份位置` 有值、云盘也有文件 | 状态与实物不符 | 对账按状态为准再删一次，或提示改回在库 |
| 同 `paper_id` 不同 md5 | 冲突 | **不覆盖**；报 `conflict` 等人工确认（换版本可能挪页码，引文会错位） |
| 缓存文件被截断 | md5 不符 | 删缓存重下；不修改 Base |

## 9. 对账（`library.cli reconcile`）

每日一次（与配额校正同一次）：

1. 列出两个云盘目录（分页取全量）。
2. 与 Base 比对，输出 `orphan / broken / duplicate / mismatch` 四类。
3. 自动动作仅限：孤儿补行（单文件原文与有本地 push JSON 的推送件；下载后算 md5）、断链改状态（原文 `待上传` / 推送件 `回填`）。
4. `duplicate` 与 `mismatch` 只报告，不自动处理。
5. 报告写到 `<运行时区>/library/reports/reconcile-<ts>.json`；有差异时退出码 1。

## 10. CLI

```bash
python -m library.cli put-source --file <pdf> --paper <paper_id> [--dry-run]
python -m library.cli put-push --push <push_id> --html <path> [--dry-run]
python -m library.cli resolve --paper <paper_id>
python -m library.cli quota
python -m library.cli reconcile [--dry-run] [--verify]
python -m library.cli evict --candidates [--limit N]
python -m library.cli evict --execute [--backup-root ~/frontier-offline]   # 备份的是拼接后的整份 PDF
python -m library.cli restore --paper <paper_id>
python -m library.cli cache prune --max-size 2G
```

`--dry-run` 对全部写操作可用；全局 `--offline` 把 Drive/Base 换成 `<运行时区>/library/offline-*.json` 的持久化夹具，不调用 `lark-cli`（`setup --offline` 会建一套假的四表与目录）。

## 11. 迁移清单

1. 建三张表（原文 / 推送日志 / 决定）+ 配置行；`papers` 改名「报道」并加原文关联。
2. 建两个云盘目录，token 写入配置。
3. 174 篇 PDF：`put-source` 批量导入，记录 `skipped / uploaded / failed` 报告。
4. 证据中指向测试推送的 `push_id` 置空：`python scripts/migrate_drop_test_push_refs.py`，`paper_id` 保留；Base 里对应的旧报道行删除（测试阶段产物）。
5. 双写 3 天：`publisher` 发送成功后仍写 `archive/`，同时调 `put-push`；每日对账零差异。
6. 第 4 天切读：`archive` 停止写 `raw/`、`pushes/` 权威文件；`resolve` 走缓存。
7. 15 条测试推送与本地产物按需清理，不入库。

## 12. 测试

- `FixtureDrive`：内存字典 `{dir_token: {name: bytes}}`，实现 list / upload / download / delete。
- `FixtureStore`：沿用知识层的假 Base，新增四表读写。
- 必测：配额拦截只影响原文、幂等重放、孤儿与断链对账、删除顺序（备份失败则不删）、md5 不一致的缓存重建、`无原件` 的降级路径、alert 冷却。
- 真实租户只在人工验收脚本里出现，不进 CI。
