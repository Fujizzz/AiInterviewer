# 体验岗位来源

`experience-jobs.json` 是本地排序模型同源的 100 个实验岗位，并非实时招聘职位。
用户于 2026-10-01 明确选择接入现有实验数据；运行时仍须显式配置文件路径，不会自动回退到它。

源文件为 [Final_items.csv](https://raw.githubusercontent.com/brycekan123/DualOptimization_jobrec/c742feea3730e8d34ec2e8ae7e58c8c0ee8a53fd/dataset/Final_items.csv)，
固定提交 `c742feea3730e8d34ec2e8ae7e58c8c0ee8a53fd`，SHA256：
`2207fff0d954f223496143f8a646d6314297e6b7c6be18fda4782bc292e1f352`。
导入前与既有 `research/kaggle_jobrec_v4/results/jobrec_v4/data_manifest.json` 核对哈希和 100 行数量。

仅作 CSV 到严格 JSON 契约的表示转换：两个列表使用 `ast.literal_eval`，数值按原列解析，
工作方式、技能、行业、学业阶段与 ID 保持原样。没有同义词转换、GPA 缩放、要求推断或再训练。
`job_llm_output` 原样作为介绍；源数据无岗位标题、公司或位置，标题仅标识“行业 · ID”（如 NLP · J0037）；2026-10-03 按用户要求移除标题中的“体验岗位”，
公司和位置为空，不编造真实发布方。完整模型要求位于 `requirements`。

这份实验目录不代表真实招聘效果已验证；模型与数据的研究限制见 `docs/recommendation.md`。
