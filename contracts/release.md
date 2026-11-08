# 发布流水线与发布索引

实现契约：描述 tag 约定、CI 发布流水线与 `releases/index.json` 发布索引（spec #92，ticket #98）。开发计划与验收以 GitHub Issues 为准；发行包布局见 `contracts/distribution-runtime.md`，清单字段见 `contracts/distribution-manifest.schema.json`，决策见 [ADR 0010](../docs/adr/0010-distribution-packages-for-installation.md) §2、§5、§7。

所有 JSON 顶层带 `schema_version: 1`，时间统一 ISO-8601（见 `contracts/README.md`）。本契约新增一个仓库内 JSON 文件：`releases/index.json`。

## 1. Tag 与渠道

发布入口只有一个：推送 `v*` tag。tag 同时决定版本与渠道，不存在 `dev`/`main` 分支发布。约定写作 `vX.Y.Z`（稳定）或 `vX.Y.Z-<prerelease>`（预发布）。

| tag | version | channel | 说明 |
|---|---|---|---|
| `v1.2.3` | `1.2.3` | `stable` | 稳定版，不标记 prerelease |
| `v0.1.0-rc.1` | `0.1.0-rc.1` | `prerelease` | 预发布 |
| `v0.1.0-preview.2` | `0.1.0-preview.2` | `prerelease` | 预发布 |
| `v0.1.0-prerelease.1` | `0.1.0-prerelease.1` | `prerelease` | 显式 `prerelease.N` 也允许 |
| `v2.0.0-beta` | `2.0.0-beta` | `prerelease` | 任意合法预发布后缀 |

规则（实现：`hostrun/release.py` 的 `parse_tag`）：

- 必须以 `v` 开头，其后是 `MAJOR.MINOR.PATCH`；数字分量与数字型预发布标识不得有前导零。
- 带 `-<prerelease>` 后缀 => `prerelease`，否则 `stable`。
- 版本进入包名/制品名时**不带** `v` 前缀：`v0.1.0-rc.1` 生成的制品是 `horizon-0.1.0-rc.1-prerelease.zip`。
- 非法 tag（缺 `v`、缺分量、四段版本、空后缀、`+build` 元数据、前导零等）在流水线第一步失败并打印原因。
- `workflow_dispatch` 的 `channel` 可选；一旦提供必须与 tag 推导值一致，否则失败（防止把预发布 tag 发到稳定渠道）。

## 2. CI 流水线 `.github/workflows/release.yml`

触发：`push: tags: ["v*"]`，以及 `workflow_dispatch`（`tag`、可选 `channel`、`dry_run` 默认 `true`）。运行器 `windows-latest`，权限 `contents: write`，checkout `fetch-depth: 0`（`built_from_commit` 需要完整历史）。步骤：

1. **推导版本/渠道**：`python -m hostrun.release tag --tag <tag> --github-output <file>`；不合法 tag 直接失败。
2. **下载固定输入**：CPython `3.13` embeddable **amd64** zip（python.org，版本由 `PYTHON_EMBED_VERSION` 固定），以及 `python -m pip download --only-binary=:all: --dest wheels -r requirements-release.txt` 下载的固定版本 wheel。`requirements-release.txt` 用 `==` 精确固定 PyYAML 与 PyMuPDF；可复现性要求不能使用运行器 site-packages，也不能用浮动区间。
3. **准备构建环境**：从同一批已下载的 wheel 安装运行器自身的构建期依赖（`python -m pip install --no-index --find-links wheels -r requirements-release.txt`），因为共享构建入口与安装冒烟用的都是运行器 Python，且 `common.py` 导入 PyYAML、PDF 链路导入 PyMuPDF；内置运行时仍使用它自己解压出的那份 wheel，构建环境与打包运行时互不替代。导入检查失败即失败。
4. **组装运行时**：`python scripts/prepare_runtime.py --embed-zip ... --wheels wheels --out runtime --python-version 3.13 --require-module yaml --require-module fitz`。它把 embeddable zip 解压到 `runtime/`，把每个 wheel 作为 zip 解压到 `runtime/Lib/site-packages/`（`*.data/purelib|platlib` 归位到 site-packages，`scripts/headers/data` 不进入运行时），并确保 `python313._pth` 含 `python313.zip`、`.`、`..`（代码根，包装器从 `<code>` 运行 `-m hostrun.cli`）与 `Lib\site-packages`，每项恰好一次。官方 embeddable zip 的扩展模块平铺在 `python.exe` 旁（不是 `DLLs/`），校验接受平铺与 `DLLs/` 两种布局。输入缺失/损坏/非空输出目录都会带原因失败，绝不产出半成品运行时。
5. **共享构建入口构建两次**：

   ```
   python -m hostrun.distribution --source . --out dist --version <ver> --channel <channel> \
       --built-from-commit ${{ github.sha }} --runtime-dir runtime \
       --os windows --arch x86_64 --python 3.13 --dsh '*'
   ```

   第二次输出到 `dist-repro/`。`--version` 也接受带 tag 前缀的 `v`（如 `v0.1.0-rc.1`）：`hostrun.distribution` 在生成包名前剥离 `v`，摘要记录 `requested_version` 与 `version_normalized`，包名/制品名永不含 `v`。随后 `python -m hostrun.release verify-artifact --zip ...` 校验 zip 含 `manifest.json`、`runtime/python.exe`、`bin/horizon.cmd`；`python -m hostrun.release check-reproducible --first-dir dist --second-dir dist-repro ...` 比对两次的制品 sha256 与 manifest sha256，不一致时打印两个 hash 并失败。
6. **安装冒烟**：构建完成后，在运行器上把制品 zip 解压到临时 bootstrap 目录，再用 `python scripts/ci_install_smoke.py --zip ... --code ... --archive ... --dsh ... --skills-target ... --check-wrapper` 依次执行 `install package --from <zip> --sha256 <制品 sha256>`、`install verify`、`console install --dsh <绝对 dummy>`、`install skill --root <code> --target <临时技能目录>`、**包装器执行检查**与 `install report`；任一命令未返回预期状态（安装为 `status=ok`、校验为 `status=verified`、控制台为 `status=ok`、报告 `release.distribution=package` 且 `skill` 步骤为 `ok`、包装器退出码 0 且 stdout 含 `usage`）就以非零退出并指明失败步骤。包装器检查通过 `cmd.exe /d /c <code>\bin\horizon.cmd --code <code> --archive <archive> --help` 执行；环境只保留 `%SystemRoot%`/`%SystemDrive%`/`ComSpec`/`PATHEXT`/`TEMP`/`TMP`，`PATH` 仅为 `%SystemRoot%\System32;%SystemRoot%`，`PYTHONHOME`/`PYTHONPATH`/`VIRTUAL_ENV`/`CONDA_PREFIX` 等一律不继承，失败 JSON 带捕获的 stdout/stderr。该步骤证明包装器只用包内运行时，而不是运行器 Python；包装器执行后还会**再跑一次** `install verify`，证明安装树被自身运行写成 `__pycache__` 字节码缓存后仍能通过校验（这些缓存与 `.horizon` 一样属于运行时状态，不计入额外文件）。它在 Release 创建与索引提交之前运行，失败即阻止发布。其余探针在 CI 中可为 pending/blocked，不做断言。
7. **创建 GitHub Release**：`gh release create <tag> --verify-tag`，上传 `.zip`、`.digest.json` 与包内 `manifest.json`。`channel == prerelease` 时加 `--prerelease` 且仅此时；`stable` 绝不带该标记。稳定/预发布是两个互斥的步骤，条件都包含 `dry_run` 判断。
8. **更新发布索引并提交到 `main`**：在重试循环**外**先解析一次 tag 提交 SHA（`git rev-parse refs/tags/<tag>^{commit}`）。随后最多尝试两次，每次顺序固定：`git fetch origin main` → `git reset --hard origin/main`（每次尝试都从干净的 `origin/main` 开始，绝不在被拒绝的本地提交上重试）→ 用 `scripts/update_release_index.py` 合并索引记录 → `git merge-base --is-ancestor <tag-commit> origin/main` 确认 tag 提交仍在 `main` 历史中 → 以 `github-actions[bot]` 提交 → `git push origin HEAD:main`。push 被拒时只重试一次；若分支保护仍拒绝 token，任务失败并说明 Release 已创建、索引需手工更新。最后 `gh release upload <tag> releases/index.json --clobber`，使 Release 也带索引。

选择「提交到 `main`」而不是只挂 Release 资产：仓库内索引可被 `git log`/代码审查审计，也能被离线读取；失败语义见上。`dry_run` 时只执行构建与校验，不创建 Release、不改索引。

## 3. 发布索引 `releases/index.json`

```json
{
  "schema_version": 1,
  "releases": [
    {
      "version": "0.1.0",
      "channel": "stable",
      "artifact_sha256": "<64 位小写 hex>",
      "artifact_name": "horizon-0.1.0-stable.zip",
      "manifest_sha256": "<64 位小写 hex>",
      "compat": {"os": "windows", "arch": "x86_64", "dsh": "*", "python": "3.13"},
      "published_at": "2026-10-31T05:00:00+00:00",
      "release_url": "https://github.com/Moke110/horizon/releases/tag/v0.1.0"
    }
  ]
}
```

约束（实现：`hostrun/release.py` 的 `validate_entry`/`validate_index`/`merge_release_index`）：

- `schema_version` 恒为 `1`；`releases` 是数组，空索引为 `{ "schema_version": 1, "releases": [] }`（本 ticket 只提交空索引，第一条真实记录由 CI 写入）。
- 条目字段固定且未知字段被拒绝：`version`、`channel`、`artifact_sha256`、`artifact_name`、`manifest_sha256`、`compat{os,arch,dsh,python}`、`published_at`、`release_url`。
- `version` 是去掉 `v` 的 semver；`channel` 必须与版本后缀一致（带后缀必须 `prerelease`）。
- `artifact_name` 必须等于 `horizon-<version>-<channel>.zip`；两个摘要必须是 64 位小写 hex；`published_at` 必须带时区；`release_url` 必须是 http(s) URL。
- 同一 `version` 再次发布时**替换**该条记录，不追加重复行；其余条目保留。
- 顺序固定为「版本新到旧」（semver 优先级：稳定版高于同版本预发布，数字标识低于字母标识）；输出为规范化 JSON（固定键序、2 空格缩进、结尾换行），相同输入产生相同字节。`published_at` 是唯一构建期时间戳。

写入入口：`python scripts/update_release_index.py --index releases/index.json --digest <digest.json> --release-url <url> [--published-at <ISO>]`。索引由构建摘要文件派生（版本、渠道、制品名/摘要、manifest 摘要、兼容范围同源），不手工拼字段。

## 4. 失败语义

| 失败点 | 行为 |
|---|---|
| tag 非法 / channel 与 tag 不一致 | 在下载前失败，打印 tag 与期望格式 |
| 下载缺失/损坏、wheel 缺失 | 失败并指明缺失的输入或 zip |
| 运行时布局不完整 | `prepare_runtime` 或 `verify-artifact` 指出缺失路径/模块 |
| 两次构建摘要不同 | 失败并打印两次 sha256 |
| 安装冒烟任一步骤失败 | `scripts/ci_install_smoke.py` 非零退出并指明失败步骤与原因；包装器失败时 JSON 带 stdout/stderr |
| 包装器无法运行或未打印 usage | `--check-wrapper` 在 Release 前失败，发布被阻止 |
| Release 创建失败 | 不写索引；任务失败 |
| 索引提交被分支保护阻止 | 重试一次后失败，说明 Release 已创建、索引需手工更新 |

真实 tag 触发、GitHub Release 创建与分支保护下的索引提交属于需要 Lead 批准的线上验收；离线测试覆盖 tag/渠道映射、索引合并与校验、运行时组装（含 `._pth` 的 `..`/site-packages 条目）、构建参数（含 `v` 前缀归一化）、zip 布局与可复现性、安装冒烟脚本（用本地构建的制品与合成运行时，不联网；`--check-wrapper` 在合成运行时上按预期失败并带 stdout/stderr，真实嵌入式运行时只在 CI 中执行）、以及本工作流 YAML 的不变量（解析而不执行）。真实 tag 上的冒烟步骤与包装器检查本身仍未在 CI 中执行过。
