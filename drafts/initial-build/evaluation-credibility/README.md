# 初始构建样例：评测、基线与可信度

保留给初始构建 CLI 和测试使用，安装时不自动写入探索者知识库。`cards.jsonl`、`propositions.jsonl`、`sources.jsonl` 是旧 v2 格式样例；正式领域词汇见 [GLOSSARY.md](../../../GLOSSARY.md) 与 [任务契约](../../../contracts/agent-tasks.md)，不要把样例格式当作当前命题结构。

- 样例含 5 张卡片、5 条候选命题、8 篇来源；可用 `python scripts/build_initial_theme_sample.py` 重建。
- 2026-10-21 探索者确认过首批构建，实际接受 5 卡、4 命题。`prop_systematic_variation_inflates_scores` 的两条证据来自同一论文，未满足至少两篇独立论文的当时门槛，留作候选。
- 历史质量门槛：卡片概念可区分，ontology 有同义词、annotation 有定义/排除/易混；命题关联至少两张卡，证据有可核对的页码和精确引文；弱主题留作候选，不为凑数入库。
- 样例里的旧 WSL PDF 路径可能不可用；缺原件时测试应标记跳过，不能据此宣称当前引文重新通过了核验。

当时的确认、质量门槛和首批构建过程见 [初始主题清单与单主题样张](https://github.com/Moke110/horizon/issues/12)、[初始构建：首批主题写入知识库](https://github.com/Moke110/horizon/issues/15)。重复的卡片/命题表和已结束的提问从本文移除，详细样例仍在 JSONL 中。
