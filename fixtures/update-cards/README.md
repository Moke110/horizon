# 飞书适配器更新题卡片样张

这些样张仅验证既有飞书卡片格式，不规定 Frontier GUI 的评分与谈判设计。

生成：`python scripts/build_update_card_samples.py`。输入为 `*.item.json`，输出为 `*.card.json`、[文字预览](preview.md) 和 [HTML 预览](preview.html)；预览不是飞书真实渲染的验收证据。

- 顶栏只写题型和进度；实体 ID 与标题放正文第一行。
- 来源随后展示；文本变更用行级 diff，证据包含论文、页码与引文，避免重复展示。
- 卡片和证据题每次处理一个实体，按钮为“同意全部 / 拒绝全部”；综述题为“采纳此版 / 保持上一版”。
- 综述全文和证据附录在 HTML 中展示，卡片仅列摘要、差异统计与 Base 链接。

卡片回调样张属于兼容能力；现行宿主流程使用持久反馈和显式提交，不因样张存在而启动事件监听。参见[运行时技能](../../skills/frontier-run/SKILL.md)与[问题队列契约](../../contracts/question-queue.md)。历史版式讨论见[旧样张说明](https://github.com/Moke110/frontier/blob/978cbfc/docs/prototypes/update-cards.md)。
