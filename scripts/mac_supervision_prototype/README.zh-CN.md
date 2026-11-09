# Mac 监管原型操作清单

这是开发实验，不是 Horizon 安装器，也不是已经解决全部后代清理问题的生产模块。
任务与验收边界：[确定 Mac 原生安装构建与桌面进程生命周期方案 #173](https://github.com/Moke110/horizon/issues/173)。

## 本次验证什么

候选方案让受控 worker 先报告就绪、收到开始信号后才工作，并观察 owner 与 broker 角色的生命期管道。
owner 或 broker 退出后，遵守协议的 worker 自行退出，不依赖唯一外部 helper 执行清理。
这里的 broker 只是持有管道的角色夹具，还没有实现真正的进程创建代理或登记身份认证。
owner、broker、worker 均由测试驱动直接创建；worker 执行 setsid，但本候选未覆盖真实后代树。
另保留忽略生命期协议的 worker 作为负向对照：用户态协议不能强制任意第三方 fork/exec 后代遵守监管。
owner 和 broker 的剩余夹具由测试驱动回收，候选没有完成整实例清理。
测试结束后的兜底回收单独记录，不能计为候选成功。

真实 Electron GUI、任意第三方进程、exec 后监管继承、完整启动竞态、PID 重用和生产集成尚未验收。
即使全部受控案例通过，仍不能解除 #173 或 #175 的阻塞。

## 1. 准备 Mac 开发环境

- 使用 Apple Silicon Mac 的原生终端；不要在 Rosetta 终端里运行。
- 需要原生 Python 3.10 或更新版本，只用标准库，无需 pip 安装依赖。
- 这是开发原型的前提；最终面向用户的安装套件仍要求内置运行时。
- 不需要 sudo、模型凭据、Horizon 安装或用户资料。不关闭 Gatekeeper/SIP。
- 将整个原型文件夹复制到 Mac，例如 `~/Desktop/horizon-mac-supervision-prototype`。路径可以含空格。

在终端执行：

```bash
cd "$HOME/Desktop/horizon-mac-supervision-prototype"
uname -m
sw_vers
python3 -c 'import platform,sys; print(sys.version); print(platform.machine())'
```

`uname -m` 和 Python 架构都应为 `arm64`。若没有 Python，先在 Mac 上配置开发用 Python；不要尝试运行 Windows 的 `.venv`。

## 2. 运行新候选

复制包根目录包含 `run.py` 时，执行：

```bash
python3 run.py --output report-01
echo "prototype exit code: $?"
```

从完整源码仓库运行时，命令是：

```bash
python3 scripts/mac_supervision_prototype/run.py --output report-01
```

故障由脚本自动注入，不必手动查 PID 或强杀 Python。运行期间保留终端，等待报告写完。
输出目录必须是新目录；第二次用 `report-02`，不要覆盖第一轮证据。
此实验仍有明确未覆盖项，非零退出码不等于脚本崩溃；以 `report.json` 中的状态、案例结果和错误字段为准。

## 3. 阅读结果

```bash
python3 -m json.tool report-01/report.json
```

- 逐项检查故障触发后受控进程是否在观察期限内退出。
- 检查开始工作前的握手失败是否阻止工作开始。
- 检查忽略生命期协议的负向对照是否暴露残留；不能删掉该失败来让报告变绿。
- 区分候选测量与测试驱动兜底回收。兜底成功不是生产清理成功。
- `blocked`、异常或回收失败必须保留原始报告；不要手动把状态改为 passed。
- 报告没有宣称完整矩阵通过时，不能把 Actions 或某个案例通过当作生产验收。

## 4. 复现旧方案的失败基线

交付包的 `baseline/mac_lifecycle_probe.py` 保留原有单 helper + 进程组方案。
在完整仓库中，该文件位于 `scripts/mac_lifecycle_probe.py`。
旧脚本在当前目录写报告，因此先建立独立目录：

```bash
mkdir baseline-report-01
cd baseline-report-01
python3 ../baseline/mac_lifecycle_probe.py
echo "baseline exit code: $?"
cd ..
```

此前原生 CI 中，无逃逸的 owner 强杀对照通过；逃逸、helper 先死再杀 owner、仅杀 helper 三项失败。
本机结果应按实际记录，不能预填。此基线没有改变生产机制。

## 5. 汇总并带回日志

只打包实验生成的报告目录，不附带配置、环境变量全集、模型内容、数据库或系统 crash dump：

```bash
tar -czf mac-supervision-report-01.tar.gz report-01 baseline-report-01
shasum -a 256 mac-supervision-report-01.tar.gz
```

将 `mac-supervision-report-01.tar.gz` 带回开发端，同时说明是否执行完整、是否中断。
没有运行基线时，从 tar 命令中去掉 `baseline-report-01`。
报告不自动上传。原型只记录实验身份、系统版本、计时和状态，不读取 Horizon 用户数据。

## 6. 结束和清理

正常运行结束时，脚本负责回收自己创建的测试进程，并在报告中记录回收结果。
出现回收失败时停止下一轮，保留报告进行定位，不使用 `killall python`、`pkill node` 或按进程名清理。
报告中的旧 PID 不能直接作为稍后强杀的依据，PID 可能已复用。

确认报告已归档、实验正常结束后，可在 Finder 中将本次复制的原型文件夹及报告移到废纸篓。
没有安装到 `~/Applications`，没有注册 launchd，也没有修改现有 Horizon 数据；无需运行 Horizon 卸载器。
若终端或测试驱动被意外强杀，记录这一情况，不能把缺失报告算通过。

## 7. 后续开发检查清单

- [ ] 在实际 Mac 连续运行两轮，检查报告及兜底回收字段。
- [ ] 核对受控案例与忽略协议的负向对照的区别。
- [ ] 验证 Python/Node/Electron 的真实工作负载如何接入登记与启动许可。
- [ ] 检查 `hostrun/desktop_worker.py`、`hostrun/agent_run.py`、`agent/src/main.ts` 及外部工具启动路径。
- [ ] 证明第三方 fork/exec 无法绕开监管，或明确记录无法实现的边界；不能只包装 spawn 函数就宣称强制隔离。
- [ ] 补齐真实 GUI、启动竞态、并发实例、无关进程隔离和 PID 重用验收。
- [ ] #173 的完整条件通过后，再进入生产模块、安装器和最终安装—运行—日志汇总—卸载套件。
