# frontier

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/Moke110/frontier?include_prereleases&label=release)](https://github.com/Moke110/frontier/releases)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0078D6)](#你需要什么)
[![Runtime](https://img.shields.io/badge/python-3.13%20bundled-3776AB)](#你需要什么)

[English](README.md) | **简体中文**

**一个替读论文、并把报道推回飞书的研究知识库。**

frontier 把你关注的主题积累成知识卡片，围绕卡片检索匹配的论文，由 agent 排序、撰写并生成带图的每日报道，推送到你的飞书。你给报道评分、并逐题确认知识更新；只有你采纳的更新才会写回知识库。久而久之，卡片、命题与证据会长成你自己的研究记忆，而不是一堆没有上下文的链接。

## 它能做什么

- **以卡片为准的检索**：每篇报道都从你确认过的知识卡片出发，而不是通用信息流，检索式因此不会跑题。
- **由 agent 写成的报道**：一次有界运行完成候选排序、写作与渲染，再以飞书文件消息投递。
- **人工确认，而不是静默改库**：agent 只提出知识更新建议；你给报道评分、逐题采纳或拒绝，只有被采纳的建议才写入权威的飞书多维表格。
- **不在你机器上常驻**：不注册 Windows 服务、不建系统定时任务、不写注册表与 PATH。唯一常驻的是一个前台控制台窗口，关掉即停止。

## 工作原理

```text
 你关注的主题 ──▶  飞书多维表格：卡片 · 命题 · 证据
                        │
                        │  检索（arXiv、Europe PMC 等）
                        ▼
                    候选论文
                        │
                        │  agent 排序、写作、渲染
                        ▼
                    一篇报道 ──▶  飞书文件消息
                        │                │
                        │                ▼
                        │        你评分并确认更新
                        │                │
                        ▼                ▼
                 采纳的更新 ◀────────────┘
                        │
                        └──▶  写回多维表格
```

知识本体存在飞书：多维表格保存卡片、命题与证据，云盘目录保存原文与推送件。本机保存的是可重建副本。资料目录**只**放 `originals/` 与 `pushes/`；配置、队列、日志、执行端租约与安装记录都放在代码目录的运行时区 `<code>\.frontier\`。

## 安装

Frontier 内置基于 Pi 开发的 Agent。你需要 Windows 10/11 x64、模型 API Key，以及有权限
配置所需应用、多维表格和云盘资源的飞书账号。发行包包含 Node.js、Pi、飞书 CLI 和 Python
业务运行时，无需另装 DSH 或 Pi。

将 Frontier 发行包解压到可写目录，在该目录打开 PowerShell：

```powershell
.\bin\frontier.cmd console install
.\bin\frontier-console.cmd
```

进入控制台后执行 `/frontier-init`：选择资料目录，填写模型服务标识、模型名和 API Key，
随后完成飞书授权、资源配置和每日推送时间设置。使用 OpenAI 兼容服务时，还需填写 API
地址；服务标识和模型名称必须与所用服务匹配。
API Key 保存在本机 `.frontier/agent/model.json`，请保护此文件；安装报告和运行参数不显示密钥。

程序目录中的 `.frontier/` 保存配置、队列和日志；独立资料目录只存放 `originals/` 与
`pushes/`。初始化中断后会续接。已有 DSH 安装需重新完成模型配置步骤，无需注册外部宿主技能。

升级经过校验的发行包可用 `install package --from <zip> --digest <digest.json>`；
`install verify` 校验安装文件，`install rollback` 恢复保留的上一版本。
升级保留 `.frontier/` 和资料目录。

## 使用

通过控制台或飞书使用：

- **控制台**（始终是主界面）：前台窗口显示定时运行、飞书消息与卡片点击、agent 运行摘要与错误；底部输入行接受下表命令。
- **飞书私聊**（控制台开着时）：发下面六条短语之一。

| 控制台命令 | 作用 |
|---|---|
| `/frontier-init` | 首次初始化；也可续接中断的初始化 |
| `/frontier-start` | 暂停后恢复服务 |
| `/frontier-stop` | 暂停定时推送与飞书响应，控制台保持在前台 |
| `/frontier-status` | 当前状态、最近一次运行与失败原因 |
| `/frontier-push` | 立即再推一篇 |
| `/frontier-update-kb` | 增量初始化知识库，逐条审阅 |
| `/frontier-logs` | 查看最近的控制台与 agent 运行日志 |
| `/frontier-help` | 命令帮助 |
| `/exit` | 退出控制台（整个服务随之停止） |

| 飞书私聊短语 | 含义 |
|---|---|
| `推一篇` | 立即推一篇 |
| `处理反馈` | 处理已提交的反馈 |
| `状态` | 当前状态 |
| `暂停` | 暂停服务 |
| `恢复` | 恢复服务 |
| `测试飞书消息` | 给自己发一条测试消息 |

只认你本人的单聊消息，其他人和群聊一律忽略。服务暂停期间只放行 `恢复`，其余消息只回暂停提示。有两条命令需要参数——修改推送时间与修改资料目录——frontier 会先做一次只读意图判定，命中白名单才发确认卡片，你点击后才执行。

每篇推送附反馈入口。先评分，再逐题确认知识更新；编辑答案后须明确提交，下次处理才生效。未回答的问题会挡住下一篇推送，但不阻止存储维护。

控制台自己计时：每天在你选的时刻推送一次（默认 08:00），错过的天数最多合并成一次补推。反馈每 5 分钟轮询处理一次；每 10 分钟从多维表格把文本改动与「待复核」勾选拉回本地权威。没有变化时保持安静。

### 初始化或扩充知识库

`/frontier-update-kb` 先暂停正式服务，让你选一个目录，由 agent 分片读取全部内容，逐条打印候选卡片与命题给你审阅：回车接受、退格丢弃、Tab 保留但打上 `Under review`。带该标记的卡片与命题在正式工作中不启用，你之后在飞书多维表格里取消「待复核」勾选，它才会参与检索与写作。它是增量的：再次执行只读资料目录里尚未登记的文件。

## 暂停、换机与恢复

`/frontier-stop`（或飞书 `暂停`）暂停定时推送与飞书响应，控制台仍在前台；`/frontier-start`（或飞书 `恢复`）恢复。关闭控制台窗口则整个服务停止，飞书端不会有任何响应，直到你重新打开。重开控制台时按上次状态恢复：上次是暂停就保持暂停，上次是运行态则自动启动服务。

换机时，先停止旧端控制台，再在新端运行安装流程，选择连接已有飞书知识库，由 agent 完成接管与恢复。同一知识库同时只允许一个执行端。

本地副本可以重建，但离线备份可能是已从云盘移除的原文的唯一副本，须单独保留。卸载运行配置不会删除飞书数据；删除云端原文需要单独确认，并先完成离线备份与校验。

## 遇到问题

- **控制台启动即退出**：多为权限不足或运行时损坏。按提示以完整权限重新启动，或查看 `install report` 与 `install verify` 的结论。
- **飞书命令没反应**：控制台必须开着。关闭窗口后飞书不会响应，反馈会堆积到下次启动再处理。
- **队列不推进**：检查答案是否已明确提交，以及是否存在更早的待答问题挡住下一篇。
- **推送失败**：`/frontier-status` 会显示最近一次失败原因与尝试次数。frontier **不会自动重试**：确认原因后用 `/frontier-push` 重新请求。
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
.\bin\frontier-console.cmd
```

TypeScript Agent 使用 Pi SDK，通过明确的工具接口调用现有 Python 业务模块。
Python 保留现有校验、存储和 PDF 处理。用 `node agent/dist/main.js --check` 检查 Agent，
用 `python -X utf8 -m pytest -q` 运行回归测试。构建发行包前需构建 Agent 并准备两种内置运行时。


## 文档与许可证

- 内置 Agent 的业务工作流：[skills/frontier-run/SKILL.md](skills/frontier-run/SKILL.md)
- 开发指引：[AGENTS.md](AGENTS.md) · 项目术语：[GLOSSARY.md](GLOSSARY.md)
- 模块契约：[contracts/](contracts/README.md)
- 许可证：[MIT](LICENSE)

## 桌面界面

日常启动请双击 `Frontier.lnk` 快捷方式（扩展名是小写 L 开头的 `.lnk`，Windows 通常隐藏它）。运行 `scripts/install-desktop.ps1` 可重新生成；使用 `-ShortcutDirectory <文件夹>` 可在指定文件夹额外创建“启动 Frontier”快捷方式。源码启动需要 Python、已安装并构建的 `desktop/`，不需要 Tkinter。

`Frontier.cmd` 保留为 CLI 入口；不传参数也会启动 GUI，但可能短暂显示命令行窗口。中央区域与内置 Agent 对话，左栏切换会话，Console 页签输入命令及交互回答。Enter 发送、Shift+Enter 换行；停止按钮取消当前 Agent。会话保存在 `.frontier/desktop/`。

首次启动会把随附 Electron 运行时复制到 `%LOCALAPPDATA%/Frontier/desktop-runtime/`，避免工作区磁盘上的 Windows 沙箱启动失败。代码、配置和资料库仍在原位置，渲染器沙箱保持开启。关闭 GUI 后，其后台工作进程会在 10 秒内结束。

### 桌面界面开发

在 `desktop/` 运行 `npm ci`、`npm run typecheck` 和 `npm run build`，然后打开快捷方式，或运行 `python -m hostrun.desktop --code <code> --archive <archive>`。Python 管理带随机令牌认证的本机回环通信和 Windows 进程作业。启动错误记录于 `.frontier/desktop-error.log`，Electron 日志位于 `.frontier/desktop-electron.log`。
