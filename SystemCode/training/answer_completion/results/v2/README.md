# V2：微调编码器，原定粗筛验收通过

私有 Kaggle GPU Notebook 第 2 版完成固定五轮；第五轮发生一次用户明确批准处理的
AMP 溢出，缩放减半、跳过该批更新，日志保留。选择验证 BCE 最低的第 2 轮权重。
报告记录基础 revision、依赖、T4 设备、数据摘要、验证阈值和所有留出集预测。

新合成测试中英正类召回均 100%，负类拦截 72.58%；新语义对照正类召回 97.5%，
负类拦截仅 10%。通过的是**粗筛**门槛，不能据此独立结束回答或宣称真实场景准确率。
模型、阈值和既定验收标准未依据对照结果修改；Qwen 确认仍为必需。

`runtime-cpu.json` 是本机真实产物的分类耗时/批量一致性检查。
`qwen-local-failed.json` 保留首次本机三秒超时，没有重试或增加期限。
服务器第一次两层检查有 3 个漏判、没有误报，之后针对项目/回答的指代关系修正 Qwen
提示，保留前后提示和结果。已观察过的 80 条对照随之成为回归集，不能声称仍独立。
新项目/否定措辞只作补充冒烟，不是独立人工金标准或真实 ASR 基准。

最终服务器回归为 79/80，唯一漏判来自原粗筛，零误结束/超时；新措辞为 20/20。
所有修订保留门槛和 3 秒 API 期限，不把已知回归重新包装成独立测试。

大模型文件不进入 Git；校验绑定的 ZIP 和完整输出保存在私有 Kaggle Notebook，
本机模型在 `output/answer-completion/trained-v2`，服务器模型目录见部署说明。

`speech-provider-beijing.json` 是保持原模型、音频格式与期限的真实北京 TTS/ASR 检查。
`production-synthetic-speech-completion.json` 是生产 HTTPS 上一条 8.88 秒虚构英文音频：
真实 ASR、本地粗筛、Qwen、三秒静默和最终签名凭据通过，最后一次转写后等待 3.008 秒。
PCM 发送完毕至通知的 7.895 秒还包含 ASR 定稿延迟，不能等同三秒端到端延迟。

`production-mcp-first-failed.json` 保留首次追加联调在第二条 STT 握手处的 10 秒超时，
无自动重试或加时。独立无模型双连接检查通过（0.102 秒），随后单独诊断在首题后增加
Agent ping/HTTP 健康检查，未修改供应商条件；连接、语音、MCP 与评分均实际执行。
`production-synthetic-speech-mcp-diagnostic.json` 保留诊断脚本的断言失败：脚本要求保存
原始首尾空白，但接口已有 `str_strip_whitespace` 契约；其清理还遗漏保护反馈请求的
`AgentTurn` 依赖。`production-mcp-persistence-cleanup.json` 记录实际请求已 succeeded、
评分与下一题均保存，随后按该虚构账号精确删除关联记录。生产逻辑/模型参数未改变。

按既有空白处理契约修正脚本断言和关联清理顺序后，单独最终验证通过，记录为
`production-synthetic-speech-mcp-final.json`：转写后静默 3.025 秒，签名绑定有效，真实
MCP succeeded、评分保存并返回下一题；虚构账号、会话和面试关联数据已清理。
供应商参数、音频、模型阈值和期限均保持不变。以上是合成单例的接通证据，不能当作
真人麦克风、噪声、ASR 准确率或独立泛化评测。
