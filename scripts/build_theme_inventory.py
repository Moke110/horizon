#!/usr/bin/env python3
"""Cluster the PDF manifest into initial-build themes.

Writes fixtures/theme-inventory.jsonl and its concise fixtures/theme-inventory.md summary.
Classification is title/category based; it is a planning aid, not a final
ontology. Overlaps are resolved by rule priority (first match wins).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common import slug  # noqa: E402

MANIFEST = ROOT / "fixtures" / "pdf-manifest.jsonl"
OUT_JSONL = ROOT / "fixtures" / "theme-inventory.jsonl"
OUT_MD = ROOT / "fixtures" / "theme-inventory.md"

THEMES = {
    "evaluation-credibility": {
        "title": "扰动预测的评测、基线与可信度",
        "description": "基准、分布外评测、简单基线、系统性变异与指标泄漏。",
        "cards": ["card_perturbation_benchmark", "card_ood_generalization", "card_simple_baseline", "card_foundation_model_eval", "card_systematic_variation"],
        "propositions": 5,
        "strength": "strong",
    },
    "perturbation-data": {
        "title": "Perturb-seq / CRISPR 筛选与扰动数据资源",
        "description": "单细胞扰动数据生成、Perturb-seq / CITE-seq 筛选、公开数据资源与图谱。",
        "cards": ["card_perturb_seq", "card_crispr_screen", "card_perturbation_atlas", "card_data_resource"],
        "propositions": 4,
        "strength": "strong",
    },
    "foundation-models": {
        "title": "单细胞基础模型与表征学习",
        "description": "scGPT / Geneformer / scBERT / scPRINT / CellFM 等基础模型与通用细胞 embedding。",
        "cards": ["card_single_cell_foundation_model", "card_gene_embedding", "card_transfer_learning"],
        "propositions": 4,
        "strength": "strong",
    },
    "drug-response": {
        "title": "药物扰动与转录组响应预测",
        "description": "化学扰动、L1000/CMap、剂量响应、药物重定位与转录组签名预测。",
        "cards": ["card_drug_induced_transcriptome", "card_l1000", "card_drug_repurposing", "card_dose_response"],
        "propositions": 4,
        "strength": "strong",
    },
    "perturbation-prediction": {
        "title": "单细胞扰动响应预测方法",
        "description": "遗传/化学扰动下的表达预测：VAE、GAN、optimal transport、diffusion、causal 模型。",
        "cards": ["card_single_cell_perturbation", "card_perturbation_response_prediction", "card_combinatorial_perturbation", "card_cell_state_representation"],
        "propositions": 5,
        "strength": "strong",
    },
    "virtual-cell-translation": {
        "title": "虚拟细胞与转化应用",
        "description": "虚拟细胞定义、验证与临床前应用；数字孪生与世界模型。",
        "cards": ["card_virtual_cell", "card_virtual_cell_validation", "card_preclinical_translation"],
        "propositions": 3,
        "strength": "medium",
    },
    "ai-drug-discovery": {
        "title": "AI 药物发现应用",
        "description": "AI 驱动的化合物生成/筛选、靶点去卷积、老药新用与疾病模型应用。",
        "cards": ["card_ai_drug_discovery", "card_target_deconvolution", "card_molecule_generation", "card_phenotypic_screening"],
        "propositions": 3,
        "strength": "medium",
    },
    "cardiac-liver-omics": {
        "title": "心脏/肝脏多组学与组织再生",
        "description": "心肌梗死、心肌肥厚、心衰、肝再生等单细胞/空间多组学研究。",
        "cards": ["card_myocardial_infarction", "card_cardiac_hypertrophy", "card_liver_regeneration", "card_immune_fibroblast_crosstalk"],
        "propositions": 4,
        "strength": "medium",
    },
    "multimodal-spatial": {
        "title": "空间与多模态扰动",
        "description": "空间 CRISPR、Perturb-CITE-seq、形态学与多组学联合建模。",
        "cards": ["card_spatial_perturbation", "card_multimodal_single_cell", "card_morphology_response"],
        "propositions": 3,
        "strength": "medium",
    },
    "toxicity-safety": {
        "title": "毒性、安全与转化风险评估",
        "description": "心脏毒性、器官毒性、环境化学物与 QT 风险预测。",
        "cards": ["card_cardiotoxicity", "card_organ_toxicity", "card_transcriptomic_safety"],
        "propositions": 3,
        "strength": "medium",
    },
    "aip-hmbs": {
        "title": "AIP / HMBS 与血红素合成",
        "description": "HMBS 缺陷、卟啉前体积聚与急性间歇性卟啉症。",
        "cards": ["card_hmbs", "card_aip", "card_heme_biosynthesis"],
        "propositions": 1,
        "strength": "weak",
    },
}

RULES = [
    ("aip-hmbs", r"porphyri|hmbs|hydroxymethylbilane"),
    ("evaluation-credibility", r"benchmark|scarchon|systema|does not yet outperform|simple controls|systematic comparison|systematic variation|systematic evaluation|comparison of computational|evaluation"),
    ("foundation-models", r"foundation model|scgpt|geneformer|scbert|scprint|cellfm|bulkformer|sclong|genecompass|universal cell embedding|large-scale pretrained|large language model embeddings|large-scale foundation|predicting cellular responses to perturbation across diverse contexts with state"),
    ("perturbation-data", r"perturb-seq|perturb-cite|scperturb|atlas|connectivity map|data portal|harmonized|screens|resource"),
    ("toxicity-safety", r"cardiotox|toxicity|hazard|qtc|proarrhythmic|safety|environmental chemical|organ toxicity"),
    ("multimodal-spatial", r"spatial|multimodal|cite-seq|morpholog|multi-omic|celcomen|multi-omics"),
    ("virtual-cell-translation", r"virtual cell|world model|digital twin|perturbation proteomics|preclinical|t-world|virtual human cardiomyocyte"),
    ("drug-response", r"drug|chemical|compound|l1000|transcriptional profile|mechanism of action|repurpos|dose-dependent|pharmac|sensitivity|chempert"),
    ("ai-drug-discovery", r"de novo|discovery of|inhibitor|rescues|prevents|ameliorates|treats|therapeutic|target deconvolution"),
    ("cardiac-liver-omics", r"cardiac|myocard|heart failure|hypertroph|ischemia|liver regeneration|kidney"),
    ("perturbation-prediction", r".*"),
]


def paper_id(row: dict) -> str:
    stem = Path(row["path"]).stem
    return "pdf_" + slug(stem).replace("-", "_")


def classify(row: dict) -> str:
    if row.get("category") == "aip":
        return "aip-hmbs"
    if row.get("category") == "cardiac-omics":
        return "cardiac-liver-omics"
    if row.get("category") == "data-resource":
        return "perturbation-data"
    text = f"{row.get('title','')} {row.get('journal','')}".lower()
    for theme, pattern in RULES:
        if re.search(pattern, text):
            return theme
    return "perturbation-prediction"


def main() -> int:
    rows = [json.loads(line) for line in MANIFEST.read_text(encoding="utf-8").splitlines() if line.strip()]
    grouped: dict[str, list[dict]] = {key: [] for key in THEMES}
    for row in rows:
        theme = classify(row)
        grouped[theme].append(
            {
                "paper_id": paper_id(row),
                "title": row.get("title", ""),
                "year": row.get("year"),
                "category": row.get("category"),
                "doi": row.get("doi"),
                "pmid": row.get("pmid"),
                "pages": row.get("pages"),
                "path": row["path"],
            }
        )

    inventory = []
    for theme_id, meta in THEMES.items():
        papers = sorted(grouped[theme_id], key=lambda p: (p.get("year") or 0, p["title"]), reverse=True)
        inventory.append(
            {
                "theme_id": theme_id,
                "title": meta["title"],
                "description": meta["description"],
                "paper_count": len(papers),
                "evidence_strength": meta["strength"],
                "proposed_cards": meta["cards"],
                "proposed_propositions": meta["propositions"],
                "papers": papers,
            }
        )
    inventory.sort(key=lambda row: (-row["paper_count"], row["theme_id"]))
    OUT_JSONL.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in inventory) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Initial-build group inventory",
        "",
        f"Source: `fixtures/pdf-manifest.jsonl` ({len(rows)} unique papers). Generated by `scripts/build_theme_inventory.py`.",
        "",
        "Classification is title/category based (first matching rule wins); it is a planning inventory, not a final ontology. Initial-build groups are temporary confirmation groups, not stored Themes (命题).",
        "",
        "| Initial-build group | Papers | Strength | Proposed Knowledge Cards | Proposed Themes |",
        "|---|---:|---|---:|---:|",
    ]
    for row in inventory:
        lines.append(
            f"| {row['title']} (`{row['theme_id']}`) | {row['paper_count']} | {row['evidence_strength']} | {len(row['proposed_cards'])} | {row['proposed_propositions']} |"
        )
    lines.append("")
    lines += ["", "完整论文与卡片建议见 `theme-inventory.jsonl`；主题需用户确认，不自动导入正式知识库。"]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT_JSONL.relative_to(ROOT)} and {OUT_MD.relative_to(ROOT)}")
    for row in inventory:
        print(f"{row['paper_count']:3d}  {row['theme_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
