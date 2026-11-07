# Frontier 内置运行时

> 实现范围：本文保留现有模块的字段、命令、校验和业务流程。产品定位与交互以[已确认决策](../../../docs/adr/0001-product-direction.md)为准；论文专用字段、飞书适配与现有确认门禁不构成新产品的全局限制。执行现有模块时仍遵守其校验，不据此绕过门禁。

用户安装 Frontier 发行包；Node.js、Pi Agent、飞书 CLI 和 Python 业务运行时一并提供。
无需安装 DSH、注册外部 Agent 技能或配置 headless overlay。

在程序目录运行 `bin/frontier.cmd console install` 生成控制台入口，然后打开
`bin/frontier-console.cmd`，执行 `/frontier-init`。初始化在 Frontier 内完成模型
API Key 配置与飞书绑定。已有 DSH 初始化记录会重新要求模型配置，其余飞书状态保留。

模型配置保存在 `.frontier/agent/model.json`，不要读取、展示或把密钥写进任务文件。
更改模型时暂停服务，由用户修改该配置；不通过 Agent 工具读取或修改凭据。

业务运行通过 Frontier 自带 Pi SDK 发起。意图判定禁用全部工具；业务运行使用
`frontier_cli` 调用指定 Python 业务模块，以 `read_file` 和 `write_file` 操作任务产物。
它不加载用户全局 Pi 设置、扩展或开发仓库的 AGENTS.md。

源码开发需要 Python 3.13 与 Node.js >=22.19.0，具体安装命令见根目录 README。
发行包升级使用 `install package --from <zip> --digest <digest.json>`，校验使用
`install verify`，回退使用 `install rollback`。配置和资料目录保留。
