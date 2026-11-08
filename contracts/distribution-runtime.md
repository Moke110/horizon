# 内置运行时与启动包装器

完整桌面发行构建要求预备运行时含 `components.json`：构建准备命令
`python -m hostrun.runtime_inventory --runtime <runtime> --node-license scripts/licenses/node-24.16.0.txt`
实际执行包内 Python/Node 的版本查询，校验固定版本并记录二进制摘要和许可文件。
构建器再次核对摘要、Python/Node/Pi/Electron 固定版本及 Electron 二进制版本文件，
将随包 npm/Python 依赖的版本、声明许可及许可文件路径汇总到 `runtime-components.json`。
该清单与许可文件受普通发行 manifest 摘要覆盖；缺少声明时明确记为未声明，
不推断许可或作法律保证。部分无桌面测试包不声称包含完整运行时清单。

实现契约：描述发行包里的 `runtime/` 目录、`bin/horizon.cmd` 启动包装器，以及清单如何覆盖它们（spec #92，ticket #95）。开发计划与验收以 GitHub Issues 为准；发行包清单与安装锁的机器可读契约见 `contracts/distribution-manifest.schema.json` 与 `contracts/install-lock.schema.json`。

所有 JSON 顶层带 `schema_version: 1`，时间统一 ISO-8601（见 `contracts/README.md`）。运行时、包装器和运行时组件清单都是普通包文件，由发行清单逐文件摘要覆盖；升级恢复日志与版本标记保存在数据 home。

## 1. 目标

探索者机器上不需要 git、uv、系统 Python 或 PATH 配置。发行包自带解释器与依赖，启动包装器只引用包内路径。

- 构建入口：`python -m hostrun.distribution --source <tree> --out <dir> --version V --channel C --runtime-dir <runtime>`
- 构建不联网、不下载、不合成运行时。CI（#98）准备 `runtime` 目录后传给构建入口。
- 不传 `--runtime-dir` 时保持 #93 的包布局：没有 `runtime/`，也没有包装器；发布构建总是传。

## 2. `runtime/` 目录布局

构建入口把 `--runtime-dir` 指向的目录**逐字节**复制为 `<package>/runtime/`。目录必须含 `python.exe`，否则构建在写任何包目录之前失败。期望布局（CPython 3.13 embeddable + 固定版本依赖 wheel）：

```
<package>/runtime/
  python.exe                       # 包装器执行的内置解释器
  python313.dll                    # 核心 DLL
  python313._pth                   # 模块搜索路径：python313.zip、.、..、Lib\site-packages
  python313.zip                    # 标准库
  *.pyd                            # 扩展模块：官方 embeddable zip 平铺在 python.exe 旁；
                                   # 完整发行版布局则放在 DLLs/ 下（两种都接受）
  Lib/site-packages/               # PyYAML、PyMuPDF 及其依赖
```

`python313._pth` 是隔离模式（isolated embeddable）的唯一 `sys.path` 来源，必须含 `python313.zip`、`.`、`..` 与 `Lib\site-packages`，每项恰好一次。`..` 相对 `runtime/` 指向代码根：包装器 `cd` 到 `<code>` 后执行 `runtime\python.exe -m hostrun.cli`，隔离模式不会把当前目录（`sys.path[0]`）放进搜索路径，缺 `..` 时 `import hostrun`/`common` 会在真实安装中失败。`scripts/prepare_runtime.py` 保留 embeddable zip 原有的 `python313.zip`/`.` 条目并补齐、去重这两项；同一运行时目录重复组装不会累积重复条目。

复制规则（保证同一运行时目录两次构建字节一致）：

- 按相对路径排序遍历，`shutil.copyfile` 保留文件字节。
- 跳过 `__pycache__/` 目录与 `*.pyc` / `*.pyo`；不跟随符号链接。
- 不写时间戳、不重写文件内容；构建产物与源目录其余元数据无关。

## 2.1 就地升级与运行中解释器

升级/回滚由已安装的包装器驱动，因此执行升级的那个内置解释器正持有 `python.exe`、`python3XX.dll` 与已加载的 `*.pyd`，Windows 会锁住这些文件。安装/回滚在写每个文件前先比较字节：**内容相同的文件直接跳过写入**。固定版本的运行时在版本之间字节一致，所以就地升级不会重写正在运行的解释器；只有 `manifest.json`、代码文件等不同内容才写入。

桌面安装程序使用新包在临时目录中的内置解释器执行 `hostrun.desktop_upgrade`，不使用目标目录中的解释器替换自身。升级持有稳定数据 home 的 `desktop.lock`；用户必须先退出桌面。数据 home 与代码目录不得相互嵌套。升级先备份原安装树和数据 home（SQLite 使用 backup API），安装校验成功后执行公开 AgentService 初始化，最后以 fsync 和 replace 提交 `version.json`。schema 初始化或版本提交失败时恢复原数据与原代码树，新安装失败则隔离失败代码目录。备份保留在 home/upgrade-backups 和安装目录相邻的 pre-upgrade 目录，不清空每阶段数据。

## 3. 启动包装器 `<package>/bin/horizon.cmd`

内置运行时存在时，构建入口生成该文件。它是一段固定文本，不是安装步骤产物：

```
@echo off
cd /d "%~dp0.."
"%~dp0..\runtime\python.exe" -m hostrun.cli %*
```

契约：

- 字节为 CRLF，首行 `@echo off`，末行以 `%*` 结尾；生成器 `hostrun.launcher.write_wrapper` 以 `newline=""` 写出。
- 解释器路径相对包装器本身（`%~dp0` 是批处理所在目录），因此移动/重命名整个代码目录仍可用。
- 不依赖 PATH，不出现裸 `python` / `py`；全部路径带引号。
- `%*` 原样转发参数；包装器不解析、不改写 argv。

## 4. 控制台启动脚本（安装后生成，不在包内）

`console install` 仍生成 `<code>/bin/horizon-console.cmd`。两种模式：

源码安装（目录没有发行包安装锁）：

```
title horizon-console
cd /d "<code>"
"<记录的解释器>" -m hostrun.cli --code "<code>" --archive "<archive>" console %*
```

发行包安装（`<code>/.horizon/dist.lock.json` 的 `distribution == "package"` 且 `<code>/bin/horizon.cmd` 存在）：

```
title horizon-console
call "%~dp0horizon.cmd" --code "<code>" --archive "<archive>" console %*
```

两种形式都以 `title horizon-console` 开头、以 `%*` 结尾，重复生成只在文本变化时重写。`horizon-console.cmd` 是安装期生成物，不属于发行包：`install verify` 只把这一固定路径列入忽略集，其他清单外文件仍报告为多余。

## 5. 清单覆盖

运行时文件与控制台包装器都在清单组装**之前**写入包目录，因此：

- `manifest.json` 的 `files` 自动包含 `runtime/**` 与 `bin/horizon.cmd`，逐文件 `bytes` / `sha256` / `executable` 与其他文件同规则。
- `install verify` 按清单校验这些文件；缺失、字节或摘要不符都会报告 mismatch。
- 清单 schema 不变（`schema_version: 1`），摘要文件与 zip 也不新增字段。

## 6. 探测规则

`install check` 的依赖探测：

- 源码安装：解释器必须位于代码目录之外，否则 `blocked`。
- 发行包安装：安装锁记录 `distribution: "package"` 且解释器解析到 `<code>/runtime/` 内时，探测照常运行 PyYAML/PyMuPDF 导入，并给出 `evidence.bundled_runtime = true`。

真实“无系统 Python 运行包装器”的证据由发布工作流的 `--check-wrapper` 步骤在真实嵌入式运行时上产生：它以净化环境（`PATH` 仅 `%SystemRoot%\System32;%SystemRoot%`，无 `PYTHONHOME`/`PYTHONPATH`/venv）执行安装后的 `<code>\bin\horizon.cmd --code ... --archive ... --help` 并要求退出码 0 且 stdout 含 `usage`。离线 pytest 只用合成运行时验证该检查会按预期失败并带 stdout/stderr，不能代替真实 tag/CI 证据；真实模板安装仍保持 `untested`。
