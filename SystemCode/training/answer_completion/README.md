# 服务器语义粗筛的合成数据与 Kaggle 训练

任务为判断“此刻是否表达结束自己对当前问题的回答”。本地模型只控制候选是否进入
Qwen Flash，不直接调用 MCP。它使用预训练多语 MiniLM 的上下文语义向量和训练得到的
线性二分类头，没有关键词表或正则结束话术。权重、阈值和编码器都来自记录的模型产物。

`experiment.json` 在训练前固定基础模型/INT8 文件、随机种子、组划分、训练参数及验收条件。
字符尾部 400、token 尾部 128、掩码均值池化和 L2 归一化由
`agents/completion_gate.py` 同时用于量化产物评估和服务器推理。下面先保留第一轮流程；
服务器接受 V2 契约，第一轮失败产物及其原源码快照仍保存在私有 Kaggle Dataset 中。

## 数据流程

```powershell
.venv/Scripts/python.exe training/answer_completion/prepare_data.py --output output/answer-completion/data
.venv/Scripts/python.exe training/answer_completion/annotate.py --input output/answer-completion/data --output output/answer-completion/audited-data
```

生成器创建 160 个来源组，中英各 80 组，每组 10 个共享回答场景的变体：4 类拟结束表达、
6 类拟非结束表达。训练/验证/测试按来源组提前分配为 120/20/20 组。
实际文本可能不符合生成类别，因此**训练标签取第二轮盲标结果**：标注模型只看 ID 和实际
文字，不看生成类别/原标签，并提供判断理由。原始标签、原文、盲标理由均保留。

数据全部为虚构合成内容，没有上传真实用户回答或用公共话轮测试集改写标签。
生成和标注都使用 Qwen Plus，后者是单独提示下的盲标，不是独立模型或人工真值。
编码助手抽查不能代替人工标注；真实面试和 ASR 数据仍需另行评测。

原始生成结果逐组保存；请求失败或格式、长度、重复检查失败会停止，不自动重试。
已完整保存的来源组可用 `--checkpoints-only` 重建。若需要经审查的数据改写，可显式指定
`--corrections <文件>`；每项修正必须绑定 ID 和原文字节摘要，不改变来源组、划分或标签。
本次 `data_corrections.json` 记录两条完全重复文本的语义等价改写，仅适用于记录的原始摘要。
它不会自动应用到未来随机生成的文本。

`challenge.jsonl` 是训练开始前由编码助手另写的 80 个中英语义对照样例。
对照复用“完成 / 结束 / nothing more”等词语，区分本人结束作答与引用、否定、任务完成、
继续补充；不会用于训练或阈值选择。它也不是独立人工金标准。

## Kaggle 输入和执行

私有 Dataset 的显式上传文件为：

- `data.jsonl`：盲标后的实际训练/验证/测试样例。
- `experiment.json`、`provenance.json`：固定条件和标签来源。
- `challenge.jsonl`：单独评测文本。
- `encoder.onnx`、`tokenizer.json`：固定版本的 INT8 编码器及分词器。
- `completion_gate.py`：服务器编码器源码副本。
- `source-hashes.json`：准备/标注/训练/编码源码摘要。

不上传 `.env`、API key、真实面试正文、数据库或整个项目目录。模型卡声明基础模型为
[Apache-2.0](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2)。
Kaggle Dataset 使用私有可见性及 other 许可证元数据，不对用户新生成的数据作公开授权。

Windows 上从上传目录执行 `kaggle datasets create -p . --keep-tabular`；CLI 2.2.4 对带 `/`
的相对目录生成上传缓存路径时会出错。已存在的数据集更新使用 `kaggle datasets version`。
Notebook 必须使用与 Dataset 不同的标题；本次 Dataset 为
`heropig/answer-completion-semantic-v1-20261003`，训练 Notebook 为
`heropig/answer-completion-semantic-training-v1-20261003`。

Notebook 上传 `train.py`，显式私有、CPU-only、开启互联网以安装固定依赖，绑定上述 Dataset。
脚本根据唯一的 experiment ID 定位挂载目录，不能用未确认的旧扁平目录假设。
基础编码器在输入包中，不在训练或服务时重新下载。

## 训练与验收边界

只在训练集拟合 C=1、class_weight=balanced 的逻辑回归头。只在验证集选取最高阈值，
要求中英分别达到至少 99% 的正类召回；测试和语义对照集在阈值固定后评估。
发布条件：测试中英召回各至少 95%、测试负类拦截率至少 50%、对照集中英召回各至少 95%。
失败时保留模型和报告，但 `release_approved=false`、训练退出非零、服务器拒绝加载。
不得依据测试结果降低阈值、修改验收条件或调整标签以制造通过。

输出 ZIP 包包含编码器、分词器、JSON 权重/阈值、manifest、报告与来源条件。
没有 pickle/joblib 可执行反序列化，也不依赖服务器上的 PyTorch 或 scikit-learn。
延迟报告为指定 CPU 设备上的编码耗时；合成留出集指标不能推断真实面试效果。

服务器配置与失败语义见[结束检测流程](../../backend/docs/answer-completion.md)。

## 已批准的 V2 编码器微调

用户明确批准补充数据并微调编码器。`experiment-v2.json` 为独立固定条件，不覆盖 V1。
`data-v2-profile.json` 覆盖真正的间接结束、移交话轮、能力边界、项目未完但本人答完，
以及同词引用/否定/继续补充。新的 160 个来源组按原种子预分配，生成 1,600 条并盲标。

```powershell
.venv/Scripts/python.exe training/answer_completion/prepare_data.py --config training/answer_completion/experiment-v2.json --profile training/answer_completion/data-v2-profile.json --output output/answer-completion/v2-generated
.venv/Scripts/python.exe training/answer_completion/annotate.py --input output/answer-completion/v2-generated --output output/answer-completion/v2-audited
.venv/Scripts/python.exe training/answer_completion/build_v2_dataset.py --v1 output/answer-completion/audited-data --v2 output/answer-completion/v2-audited --output output/answer-completion/v2-combined
```

仅显式指定 `--resume-checkpoints` 才继续缺失组，已生成组保持原文；API 错误没有自动重试。
结构检查先保存原始组，长度/重复检查在最终组装执行；短片段与重复文本经过显式审查，
通过 `--checkpoints-only --corrections training/answer_completion/data-v2-corrections.json` 重建。
该记录把同场景已有内容与结束语拼接，保留结束意图和原摘要，不改变来源组/划分/类别。
盲标仍独立判断新文本，而不把类别或出现词语直接当作真实标签。

V1 只保留 1,200 条训练行，组合后为训练 2,400、全新验证 200、全新测试 200。
`challenge-v2.jsonl` 是训练前另写的 80 条新语义对照，第一轮对照为已知回归检查。
组之间不会跨划分；主题池可以重复，因此这是按来源场景留出，并非完全未见主题测试。
源数据、代码、提示、配置和对照摘要在上传前固定。

V2 私有 Dataset 上传合并数据、配置/来源、挑战/回归集、基础分词器以及
`completion_gate.py`、`train.py` 指标助手与 `train_v2.py`。私有 GPU Notebook 入口为
`train_v2.py`，显式使用 T4；精确版本的基础权重只在训练时下载，不在服务器运行时下载。
五轮微调全部编码器和二分类头，选验证加权 BCE 最低轮，随后导出固定 batch=1、序列长度
可变的 ONNX，按配置以 QUInt8 量化 MatMul/Gather。CPU 阈值校准与所有验收输入也是单条。
V1 的每语言召回与负类拦截门槛原样保留；失败产物不能加载，不能调低标准冒充成功。

V2 第一次运行第五轮因严格梯度裁剪抛错中止，未做留出集评估，结果在
`results/v2-aborted`。用户明确批准改为标准 AMP GradScaler 溢出处理后再运行：先取消
损失缩放，有限梯度才裁剪；非有限梯度让 GradScaler 跳过更新并降低缩放，记录批次和
每轮次数。不会切换 FP32 或变更数据/学习率/轮数/验收，非有限损失仍直接中止。

## 真实合成音频接通检查

`probe_synthetic_audio.py` 将预先固定的 `synthetic-audio-cases.json` 变成真实百炼音频，
再发送到生产 HTTPS STT；不把原文直接交给分类器，也不替换 ASR、粗筛或 Qwen。
中英文各五个单段用例覆盖直接/间接结束、否定、引用、后文继续；另测结束语后间隔
一秒补充，以及收到结束通知后仍有收尾语音。期待结果在生成音频前固定。

测试生成端显式选择 `Chinese` / `English`，其余 TTS 模型、声音、24kHz PCM16 和期限
使用现有生产配置；不会修改生产合成适配器固定的英文默认值。
语言参数依据[百炼官方 Python SDK](https://help.aliyun.com/en/model-studio/qwen-tts-realtime-python-sdk)。
音频按浏览器 `PCM16Resampler` 的面积平均及量化逻辑转成 16kHz PCM16；本机已用
满量程、零值、固定随机输入及 128 帧块边界与真实 JS 实现做逐字节一致性检查。
网络探针沿用此前合成检查的 20ms/640 字节发送节奏，不模拟真实设备的采集处理和噪声。

在已加载生产私有环境的服务器执行（须使用新的输出目录）：

```bash
/opt/ai-interviewer/current/.venv/bin/python /tmp/probe-synthetic-audio-v1.py \
  --project-root /opt/ai-interviewer/current \
  --cases /tmp/synthetic-audio-cases-v1.json \
  --output-dir /opt/ai-interviewer/preflight/synthetic-audio-20261003-v1
```

前两个 `/tmp` 文件分别由本目录脚本和用例上传得到；不上传 SSH 文件或其他本地环境。
输出记录文本/脚本/模型 manifest 摘要、每段 WAV/PCM 摘要与逐例结果，录音停止后最多
观察 15 秒，不修改服务端三秒计时、分类阈值或 API 超时。负例随后手动 stop 取得最终
转写，不能把“有限观察期内未结束”解释为永远不会触发。首个正例还会调用真实 MCP，
验证回答/评分保存和下一步，其他用例只验证真实 STT、意图与凭据，不触发评分。
临时用户、会话及面试关联记录在 finally 清理，供应商错误显式失败且不自动重试。
这是固定虚构样例的接通检查，不是训练验收或独立人工准确率评测。
2026-10-03 本轮 12 个用例全部符合预期，结果与验证边界见
[合成音频报告](results/synthetic-audio-v1/README.md)。
