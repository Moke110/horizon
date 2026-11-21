# Horizon（地平线）

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Moke110/horizon?include_prereleases&label=release)](https://github.com/Moke110/horizon/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078D6)](#你需要什么)
[![Runtime](https://img.shields.io/badge/python-3.13%20bundled-3776AB)](#你需要什么)

[English](README.md) | **简体中文**

**面向各行各业持续学习者的资料获取、知识管理与个性化解读助手。**

Horizon（地平线）将软件使用者统一称为**探索者（Explorer）**。**知识库（Knowledge Base）**包含**知识卡片（Knowledge Card）**与**命题（Theme）**。

Horizon 包含四部分：获取论文、新闻、推送、博客等原始资料；用自建知识管理套件支持知识卡片与命题的自动更新和手动维护；结合探索者已掌握的知识与积累的偏好撰写个性化解读；由 Agent 定时串联检索、写作和投递。

Horizon GUI 提供阅读、评分及围绕理解、偏好和知识更新的谈判交互。飞书是可选的外接交互与投递渠道。收藏资料不等于探索者已经掌握知识。

完整方向见已确认的[产品决策](docs/adr/0001-product-direction.md)。以下安装与命令说明描述现有实现及飞书适配链路，不代表自建知识库和新交互已经完成。

## 安装

Horizon 内置基于 Pi 开发的 Agent。你需要 Windows 10/11 x64、模型 API Key，以及有权限
配置所需应用、多维表格和云盘资源的飞书账号。发行包包含 Node.js、Pi、飞书 CLI 和 Python
业务运行时，无需另装 DSH 或 Pi。

将 Horizon 发行包解压到可写目录，在该目录打开 PowerShell：

```powershell
.\bin\horizon.cmd console install
.\bin\horizon-console.cmd
```

进入控制台后执行 `/horizon-init`：选择资料目录，填写模型服务标识、模型名和 API Key，
随后完成飞书授权、资源配置和每日推送时间设置。使用 OpenAI 兼容服务时，还需填写 API
地址；服务标识和模型名称必须与所用服务匹配。
API Key 保存在本机 `.horizon/agent/model.json`，请保护此文件；安装报告和运行参数不显示密钥。

程序目录中的 `.horizon/` 保存配置、队列和日志；独立资料目录只存放 `originals/` 与
`pushes/`。初始化中断后会续接。已有 DSH 安装需重新完成模型配置步骤，无需注册外部宿主技能。

升级经过校验的发行包可用 `install package --from <zip> --digest <digest.json>`；
`install verify` 校验安装文件，`install rollback` 恢复保留的上一版本。
升级保留 `.horizon/` 和资料目录。

## 使用

通过控制台或飞书使用：

- **控制台**（现有命令接口）：前台窗口显示定时运行、飞书消息与卡片点击、agent 运行摘要与错误；底部输入行接受下表命令。
- **飞书私聊**（控制台开着时）：发下面六条短语之一。

| 控制台命令 | 作用 |
|---|---|
| `/horizon-init` | 首次初始化；也可续接中断的初始化 |
| `/horizon-start` | 暂停后恢复服务 |
| `/horizon-stop` | 暂停定时推送与飞书响应，控制台保持在前台 |
| `/horizon-status` | 当前状态、最近一次运行与失败原因 |
| `/horizon-push` | 立即再推一篇 |
| `/horizon-update-kb` | 增量初始化知识库，逐条审阅 |
| `/horizon-logs` | 查看最近的控制台与 agent 运行日志 |
| `/horizon-help` | 命令帮助 |
| `/exit` | 退出控制台（整个服务随之停止） |

| 飞书私聊短语 | 含义 |
|---|---|
| `推一篇` | 立即推一篇 |
| `处理反馈` | 处理已提交的反馈 |
| `状态` | 当前状态 |
| `暂停` | 暂停服务 |
| `恢复` | 恢复服务 |
| `测试飞书消息` | 给自己发一条测试消息 |

只认你本人的单聊消息，其他人和群聊一律忽略。服务暂停期间只放行 `恢复`，其余消息只回暂停提示。有两条命令需要参数——修改推送时间与修改资料目录——horizon 会先做一次只读意图判定，命中白名单才发确认卡片，你点击后才执行。

每篇推送附反馈入口。先评分，再逐题确认知识更新；编辑答案后须明确提交，下次处理才生效。未回答的问题会挡住下一篇推送，但不阻止存储维护。

控制台自己计时：每天在你选的时刻推送一次（默认 08:00），错过的天数最多合并成一次补推。反馈每 5 分钟轮询处理一次；每 10 分钟从多维表格把文本改动与「待复核」勾选同步到本地副本。没有变化时保持安静。

### 初始化或扩充知识库

`/horizon-update-kb` 先暂停正式服务，让你选一个目录，由 agent 分片读取全部内容，逐条打印候选卡片与命题给你审阅：回车接受、退格丢弃、Tab 保留但打上 `Under review`。带该标记的卡片与命题在正式工作中不启用，你之后在飞书多维表格里取消「待复核」勾选，它才会参与检索与写作。它是增量的：再次执行只读资料目录里尚未登记的文件。

## 暂停、换机与恢复

`/horizon-stop`（或飞书 `暂停`）暂停定时推送与飞书响应，控制台仍在前台；`/horizon-start`（或飞书 `恢复`）恢复。关闭控制台窗口则整个服务停止，飞书端不会有任何响应，直到你重新打开。重开控制台时按上次状态恢复：上次是暂停就保持暂停，上次是运行态则自动启动服务。

换机时，先停止旧端控制台，再在新端运行安装流程，选择连接已有飞书知识库，由 agent 完成接管与恢复。同一知识库同时只允许一个执行端。

本地副本可以重建，但离线备份可能是已从云盘移除的原文的唯一副本，须单独保留。卸载运行配置不会删除飞书数据；删除云端原文需要单独确认，并先完成离线备份与校验。

## 遇到问题

- **控制台启动即退出**：多为权限不足或运行时损坏。按提示以完整权限重新启动，或查看 `install report` 与 `install verify` 的结论。
- **飞书命令没反应**：控制台必须开着。关闭窗口后飞书不会响应，反馈会堆积到下次启动再处理。
- **队列不推进**：检查答案是否已明确提交，以及是否存在更早的待答问题挡住下一篇。
- **推送失败**：`/horizon-status` 会显示最近一次失败原因与尝试次数。horizon **不会自动重试**：确认原因后用 `/horizon-push` 重新请求。
- **「正在运行」不等于已送达**：没有送达证据的定时运行会如实标记；请相信送达结果，而不是计时器。

## 开发者：源码安装

需要 Python 3.13 和 Node.js >=22.19.0。在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
npm --prefix agent ci --ignore-scripts
npm --prefix agent run build
node agent/node_modules/@larksuite/cli/scripts/install.js
.\.venv\Scripts\python.exe -m hostrun.cli console install
.\bin\horizon-console.cmd
```

TypeScript Agent 使用 Pi SDK，通过明确的工具接口调用现有 Python 业务模块。
Python 保留现有校验、存储和 PDF 处理。用 `node agent/dist/main.js --check` 检查 Agent，
用 `python -X utf8 -m pytest -q` 运行回归测试。构建发行包前需构建 Agent 并准备两种内置运行时。


## 文档与许可证

- 内置 Agent 的业务工作流：[skills/horizon-run/SKILL.md](skills/horizon-run/SKILL.md)
- 开发指引：[AGENTS.md](AGENTS.md) · 项目术语：[GLOSSARY.md](GLOSSARY.md)
- 模块契约：[contracts/](contracts/README.md)
- 许可证：[MIT](LICENSE)

## 桌面界面

日常启动请双击 `Horizon.lnk` 快捷方式（扩展名是小写 L 开头的 `.lnk`，Windows 通常隐藏它）。运行 `scripts/install-desktop.ps1` 可重新生成；使用 `-ShortcutDirectory <文件夹>` 可在指定文件夹额外创建“启动 Horizon”快捷方式。源码启动需要 Python、已安装并构建的 `desktop/`，不需要 Tkinter。

`Horizon.cmd` 保留为 CLI 入口；不传参数也会启动 GUI，但可能短暂显示命令行窗口。中央区域与内置 Agent 对话，左栏切换会话，通过模型设置配置模型。桌面 Console 页签不运行旧命令；旧命令请使用独立 CLI Console。Enter 发送、Shift+Enter 换行；停止按钮取消当前 Agent。会话保存在 `%LOCALAPPDATA%/Horizon/data/horizon.sqlite3`。可将 `HORIZON_DATA_HOME` 设置为绝对路径以指定数据目录；更换安装目录仍使用同一数据目录，不导入历史工作区。

首次启动会把随附 Electron 运行时复制到 `%LOCALAPPDATA%/Horizon/desktop-runtime/`，避免工作区磁盘上的 Windows 沙箱启动失败。代码、配置和资料库仍在原位置，渲染器沙箱保持开启。关闭窗口后应用留在托盘运行；选择“退出并暂停”才会停止其工作进程。

打开“自动任务”可创建、修改、暂停或恢复计划。填写明确的任务范围和间隔；这些开发阶段范围独立于知识库 Frontier。默认执行上限为两个主任务、四个子代理，可在同一页面调整。同一计划串行执行，已暂停的任务会阻塞该计划，直到继续或放弃。错过的触发合并为一次运行，已声明的执行覆盖范围在重启后保留；此声明不代表来源检索已获证实。缺少有效覆盖声明时，本次执行保持待复核；放弃该次执行会暂停计划，不推进覆盖范围。

“轨迹”分别记录生成和投递状态。完成回答或改写不代表授权发送。配置投递渠道后，需明确准备并发送已完成结果。投递结果不明时先核实；再次发送前必须确认重复投递风险。未配置渠道时只显示状态，不发送。

打开“资料”，粘贴文本、填写标题与来源说明，即可在没有模型凭据时离线登记。选择已保存资料可查看完整原文、来源身份、登记时间、SHA-256 与提取版本。生成摘要单独显示；登记不会生成摘要或联系远端服务。原文独立于会话保留。

在“资料”中点击“导入 PDF”，可导入不超过 10 MiB 的本地 PDF。点击页码，对照原页核实提取文字，尤其是分栏、表格与公式。缺少可读文本时，可自行补充或放弃补充。

在“资料”输入公开 HTTPS 链接，点击“获取网页正文”。保存后可查看来源链接、获取时间及带编号的正文段落，也可离线重新打开快照。若无法访问，资料会保留原链接和失败原因。

对于使用 JavaScript 显示公开正文的网页，获取前勾选“使用浏览器渲染公开动态页”。Voyager 候选也可以选择“用浏览器获取此候选的正文”。动态获取使用 Windows 安装包内置的 Chromium。

### 桌面界面开发

运行新版安装程序前请先选择“退出并暂停”。安装程序使用自身内置运行时，备份现有数据目录，并在原位置保留会话；桌面仍在运行时会拒绝替换。

在 `desktop/` 运行 `npm ci`、`npm run typecheck` 和 `npm run build`，然后打开快捷方式，或运行 `python -m hostrun.desktop --code <code> --archive <archive>`。Python 管理带随机令牌认证的本机回环通信和 Windows 进程作业。启动错误记录于 `.horizon/desktop-error.log`，Electron 日志位于 `<数据目录>/desktop-electron.log`。

桌面对话在任务完成后继续提问或重新打开 Horizon 时保留模型上下文。新建会话使用独立上下文。请在 Horizon 设置中配置模型，凭据保存在后端数据目录。

模型配置：选择提供商并输入 API Key，Horizon 会查询该凭据可见的模型，在对话框右下角选择使用的模型。刷新列表无需重新输入密钥。列表中的模型仍可能受账户权限或额度限制；自定义 OpenAI 兼容服务还需填写 API 地址。

内置 Agent 可以将有明确范围的开发工具任务交给独立子会话。轨迹页展示角色、父子会话、输入版本、稳定产物引用及成功或失败结果。这些初期只读开发角色不是 Voyager、Editor 或 Keeper。

桌面对话在进程意外退出后恢复未完成轮次，并复用已提交的子任务结果。当前开发阶段的可恢复轮次只开放只读和受限子任务工具，不自动重放旧版有写入作用的工作流。轨迹页分别显示执行、资料等待、知识提议和投递状态。

关闭窗口后 Horizon 留在托盘继续运行。选择“退出并暂停”停止；重新打开后在轨迹页点击“继续任务”。“放弃任务”取消未完成工作并保留已完成结果。

模型临时故障只有限重试。轨迹页显示认证、配置、额度等错误类别并保留成功结果。处理问题后点击“继续任务”，在原任务中重试未完成工作。

点击桌面页眉的“导出诊断”可生成本地报告文件夹，包含有限大小的生命周期、任务和错误元数据、版本及任务状态摘要。默认不导出 API Key、配置文件、对话与文献正文、数据库或 Pi 会话文件，不会自动上传。报告位于数据目录下的 `diagnostics/reports/`。若 GUI 无法启动，可用安装目录内的运行时执行 `runtime/python.exe -m hostrun.diagnostics --code <安装目录> --output <报告目录>`。

### 明确范围检索

打开“范围检索”，保存原始研究范围、中英文替代查询、必须包含与排除词以及来源限制。可选择首次基础/近期检索或带重叠窗口的增量检索，并以发表或发现时间筛选。每段默认预算为 900 秒、20 次查询、100 条候选、20 篇正文；任一达到上限均保留部分结果与缺口。继续会开启新一段预算，累计用量与此前不确定的工作仍保留。成功查询游标与缺口分别展示。临时范围不会建立 Frontier；在来源列表填写 `arxiv` 或 `europepmc` 即可检索 arXiv 或 Europe PMC。两个来源均已内置，需要联网。若某个来源查询失败，可查看其覆盖缺口及其他来源返回的结果。填写 `crossref` 可匿名检索 Crossref。使用 `openalex` 前，请在“范围检索 → 检索来源 API”中保存已验证账号的 OpenAlex API Key。OpenAlex 每次查询前检查账户当日剩余额度。候选保留来源标识、来源提供的 DOI 和最多 100 个参考文献标识；来源报告的总数可能高于列出数量。

候选列表显示检索范围命中词、可能的反证、更正及撤稿，可从候选查看查询依据。同一资料可保留多个来源，在界面中按资料身份归组。注明转载的线索用于追溯出处。全部查询覆盖且没有符合条件的新资料时，标为“无新增”。部分覆盖与失败仍显示缺口和已经取得的候选。

可选网页发现使用来源 `tavily`；在“范围检索 → 检索来源 API”中保存你自己的密钥。使用免费的 Researcher 套餐，关闭 PAYG，并为密钥设置有限的 Monthly Limit。Horizon 核实套餐后调用 Search basic。设置页显示密钥是否已保存，实际授权和额度在发起检索时检查。没有已保存的密钥时，使用启动环境中的 `OPENALEX_API_KEY` 或 `TAVILY_API_KEY`。缺少密钥、额度未知或耗尽时暂停此渠道。检索 Tavily 时选择“发现时间”，并将时间窗口的结束设在检索之后。搜索片段是发现元数据，不是全文。

若 Tavily 返回的 PAYG 或密钥上限为空，打开「Tavily 免费预算」，确认已在 Tavily 控制台关闭 PAYG，并填写该密钥的 Monthly Limit。Horizon 仍会核对免费余额。若接口报告 PAYG 用量或正数上限，检索会停止。更换密钥会清除确认。

在“范围检索 → 订阅更新”中添加公开 HTTPS 的 RSS、Atom 或栏目地址，然后点击“刷新订阅”。成功后可查看新增或更新的条目和最后成功时间，面板列出本轮前十条。要检索已保存的版本，将显示的 `sub_…` 来源标识填入范围并选择发现时间。栏目发现限于指定域名及路径，每轮最多跟进两个下一页链接。

点击“获取候选开放全文”查找学术候选的 PDF、HTML 或 XML 原文。Horizon 依次尝试来源原生文档、开放获取 DOI 查询和公开落地页。要启用 Unpaywall，请在“范围检索 → 检索来源 API”保存联系邮箱。点击“查看资料与获取记录”可读已保存的正文及来源依据，或查看仍待全文的原因。
