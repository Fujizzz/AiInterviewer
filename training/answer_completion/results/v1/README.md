# V1 研究结果：未达到发布条件

私有 Kaggle Notebook：
[Answer Completion Semantic Training V1 20261003](https://www.kaggle.com/code/heropig/answer-completion-semantic-training-v1-20261003)，
正式结果来自第 2 版；第 1 版在输入路径处失败，尚未训练。

- `report.json`：真实 Kaggle 正式训练配置、版本、指标、延迟和留出集预测。
- `manifest.json`：模型输入与文件摘要，`release_approved=false`；用于结果追溯。
- `batch-consistency.json`：同机批量与单条输入的差异及翻转样例。
- `runtime-probe.json`：本机 CPU 单条编码延迟及漏判 ID；完整虚构文本另见 challenge.jsonl。

编码器和学习权重未加入 Git；完整模型位于私有 Notebook 输出和本机
`output/answer-completion/trained-v1`。服务器已验证拒绝加载未通过验收的 manifest，
私有环境配置及线上服务未修改。不能根据这些合成结果声称真实面试准确率。
