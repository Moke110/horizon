# 桌面 GUI 更新与历史发布索引

实现契约：记录桌面 GUI 更新方式与历史 `releases/index.json` 发布索引（spec #92，ticket #98）。开发计划与验收以 GitHub Issues 为准；发行包布局见 `contracts/distribution-runtime.md`，清单字段见 `contracts/distribution-manifest.schema.json`，决策见 [ADR 0010](../docs/adr/0010-distribution-packages-for-installation.md) §2、§5、§7。

所有 JSON 顶层带 `schema_version: 1`，时间统一 ISO-8601（见 `contracts/README.md`）。本契约新增一个仓库内 JSON 文件：`releases/index.json`。

## 1. 桌面 GUI 更新

Windows 桌面 GUI 从源码工作区构建并通过 `Horizon.cmd` 重启。此流程无需发布标签、GitHub Release 或安装包。下文保留已有发行包的索引契约。

## 2. 发布索引 `releases/index.json`

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

- `schema_version` 恒为 `1`；`releases` 是数组，空索引为 `{ "schema_version": 1, "releases": [] }`。
- 条目字段固定且未知字段被拒绝：`version`、`channel`、`artifact_sha256`、`artifact_name`、`manifest_sha256`、`compat{os,arch,dsh,python}`、`published_at`、`release_url`。
- `version` 是去掉 `v` 的 semver；`channel` 必须与版本后缀一致（带后缀必须 `prerelease`）。
- `artifact_name` 必须等于 `horizon-<version>-<channel>.zip`；两个摘要必须是 64 位小写 hex；`published_at` 必须带时区；`release_url` 必须是 http(s) URL。
- 同一 `version` 再次发布时**替换**该条记录，不追加重复行；其余条目保留。
- 顺序固定为「版本新到旧」（semver 优先级：稳定版高于同版本预发布，数字标识低于字母标识）；输出为规范化 JSON（固定键序、2 空格缩进、结尾换行），相同输入产生相同字节。`published_at` 是唯一构建期时间戳。

写入入口：`python scripts/update_release_index.py --index releases/index.json --digest <digest.json> --release-url <url> [--published-at <ISO>]`。索引由构建摘要文件派生（版本、渠道、制品名/摘要、manifest 摘要、兼容范围同源），不手工拼字段。
