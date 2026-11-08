Horizon Windows 11 安装、升级与诊断验收

把整个文件夹复制到 Windows 11 x64 电脑或虚拟机，保留所有文件。
不需要安装 Python、Node、git 或 uv。请使用默认安装目录。

一、安装并建立会话
双击 1-install.cmd，按安装向导完成安装。它会自动保存安装日志。
在资源管理器地址栏输入 %LOCALAPPDATA%\Programs\Horizon，打开 Horizon 快捷方式。
在 GUI 配置模型提供商和 API Key，密钥不要发到聊天中。
新建会话，发送：
请记住 WIN11-HORIZON-131。依次委派 development_reader 和 development_summarizer 两个子会话，只处理这段合成测试文字，只调用 developer_context，不读取文件。最后回复上述标记。
确认主回复完成，轨迹里两张子会话卡成功。

二、升级并继续原会话
在 Horizon 点“退出并暂停”（旧测试版如果没有这个按钮，关闭窗口即可）。新版的关闭×只是隐藏到托盘。
双击 2-upgrade.cmd，保留原安装位置和数据。它会自动保存升级日志。
重新打开 Horizon，进入原会话，发送：请只回复我此前让你记住的完整标记。
应返回 WIN11-HORIZON-131；模型配置和两张子会话卡应仍在。

三、汇总并交回
测试完成或任何一步出错后，双击 3-collect.cmd。
将旁边整个 report 文件夹复制回来交给我。不必逐个找日志。
report/SUMMARY.txt 是总入口；安装结果在 installer-results.txt，详细安装日志是 install-*.log / upgrade-*.log。
application 内每次导出有独立文件夹，包含 summary.md、事件时间线和只读状态摘要。
如果安装失败、应用打不开，仍可收集安装器日志和系统版本；缺失的应用报告会明确标注。

应用中也有“导出诊断”按钮，点击后显示独立报告目录。为统一汇总，交回虚拟机测试结果时仍建议运行 3-collect.cmd。
默认只收集有限大小的状态日志和版本信息，不包含 API Key、对话正文、文献正文、配置文件、数据库或 Pi 会话文件；不会自动上传。
安装器日志会包含本机安装路径和安装错误。若还需要说明，直接附一句“在哪一步、看到什么”。
不要把整个数据 home、API Key、数据库或原始会话文件拷入 report。

可选最小 PATH 检查：先明确退出 Horizon，在 PowerShell 粘贴以下三行。PowerShell 会自行关闭，然后只观察 Horizon 是否额外弹出黑色控制台。
$env:PATH="$env:SystemRoot\System32;$env:SystemRoot"
Start-Process "$env:LOCALAPPDATA\Programs\Horizon\Horizon.lnk"
exit

这是本地验收包，不是公开 Release。出现错误时保留目录与 report，不要清空会话数据。
