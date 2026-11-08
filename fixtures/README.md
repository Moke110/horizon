# 测试与研究样例

这些文件是离线测试、初始构建样例和存量验收基线，不是探索者的正式知识库；安装时不自动导入。

| 内容 | 用途 |
|---|---|
| `home/`、`papers/` | 离线运行时区与论文候选 |
| `pdf-manifest.jsonl` | 历史 174 篇唯一原文的清单，供存量核对使用；原 WSL 文件路径可能已失效，清单不等于原件备份 |
| `theme-inventory.jsonl` | 按标题/类别产生的主题建议与完整论文列表，不是已确认的知识本体 |
| `theme-inventory.md` | 上述 JSONL 的简短汇总；`python scripts/build_theme_inventory.py` 可重建两者 |
| `update-cards/` | 更新卡片输入、输出和预览，见[样张说明](update-cards/README.md) |

初始构建的 JSONL 样例在 `drafts/initial-build/evaluation-credibility/`，被测试与初始构建命令使用，因此保留；不要把它当成本地 issue tracker。

历史语料研究：362 个 PDF 文件经排除非论文并按 md5 去重后形成 174 篇清单；当时均可提取正文。DOI、标题和年份不能只信 PDF 内嵌元数据，应结合来源核对；引文按 PDF 页码和原文精确定位。上述是旧语料的研究结论，不保证新文件有同样性质。

完整调研和旧主题报告可查 [PDF 研究历史](https://github.com/Moke110/horizon/blob/978cbfc/docs/research/pdf-corpus-inventory.md) 与 [主题报告历史](https://github.com/Moke110/horizon/blob/978cbfc/docs/research/theme-inventory.md)。原件、清单和衍生索引的存储规则见 [原文与推送库契约](../contracts/lark-storage.md)。
