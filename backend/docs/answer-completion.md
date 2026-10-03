# 独立回答结束检测与 MCP

网页语音面试新增自动结束当前回答的分支：用户用任意自然表达结束本题回答，
服务器上的本地语义二分类模型做粗筛，Qwen Flash 确认结束意图，并等待
**3 秒无补充**。没有关键词或正则筛选层。粗筛模型通过显式路径配置启用。
之后浏览器收尾录音、等待完整最终转写，再调用后端 `finish_current_answer` MCP 工具。
工具复用原有回答安全检查、保存、评价和下一步 Agent 决策；不会取消整场面试、跳过评分，
也不强制主 Agent 跳过正常的追问决策。手动“结束回答”仍可使用。

## 方案与调用边界

主 Agent 之外的独立模块为 `agents/answer_completion.py`。配置本地模型时，先使用
微调后导出的 INT8 多语 MiniLM 编码器与训练得到的二分类头过滤候选，再由轻型 LLM API 确认。
本地模块 `agents/completion_gate.py` 与训练脚本共用编码处理，不读取或修改主 Agent 状态。
合成数据的来源、标签检查、固定条件及验证边界见[粗筛训练记录](answer-completion-training.md)。

1. ASR 转写副本进入本地观察器，文字变化立即撤销旧决定。
2. 任何非空定稿转写都可进入本地语义模型，包括普通技术描述、否定句和自然结束表达。
   分数达到训练产物内的验证集阈值才进入独立 `qwen-flash`；本地正类不能直接结束回答。
   本地模型不检查固定话术，使用学习得到的语义向量和权重。
   尚未定稿的字幕只撤销旧决定，不发起分类。
   定稿后文字和语音稳定至少 **0.5 秒**才发送；期间连续定稿只保留最新片段。
3. 处理的只有最后至多 400 字符。本地模型保留尾部最多 128 tokens，掩码均值池化并
   L2 归一化，再计算学习得到的候选概率；线程执行不阻塞 ASGI 循环。
   Qwen 不开思考，最多生成 32 tokens，
   返回严格的 `{"finished":true/false}`。没有训练数据上传或完整面试上下文传输。
4. 同一录音最多一个分类请求在途，两次启动至少间隔 **1 秒**；重复的已发送片段不重发。
   在途期间出现新的定稿转写时只保留最新片段，待旧请求返回且满足时间条件后发送。
   过期结果不会触发结束，但调用失败仍显式报告。
5. 从被分类片段定稿开始计时，模型调用与三秒等待重叠。任何新的转写内容，或连续至少
   50ms 且 RMS >= 0.015 满量程的 PCM 活动都会撤销决定。
6. 后端通知浏览器收尾后，再核对最终转写与已确认文本完全一致。收尾时出现补充，
   就不签发自动提交凭据，网页保留最终字幕并提示核对、手动确认。

单纯静默或录音达到原有 120 秒上限不会触发自动提交。模型提示和固定语义示例要求区分
“正在总结回答内容”和“明确结束本题回答”，并将否定、引用、后续补充和单独道谢判为未结束。
这些示例只是模型上下文，不参与本地发送判断；模型可能误判，少量样例不能证明其可靠性。
项目仍在进行不等于回答仍在继续；提示分别识别项目和本人回答的指代对象。
后文明确继续解释/补充会覆盖前面的可能结束语，双重否定也按全句意图判断。
新的结束检测分支
显式使用 `zh/en` ASR 提示；旧 STT 客户端仍保持原来的英文提示。语音模型、音频编码、
录音上限、面试预算、评分及主 Agent 模型参数沿用原配置。

PCM 能量检测用于提前撤销决定，不是训练过的 VAD；背景噪声可能取消自动结束，低能量
语音仍依靠随后到达的转写撤销。真实麦克风和供应商转写延迟需要在使用设备上验证。
本地语义模型在每个 ASGI 进程内共享，CPU-only，每个编码器使用两条计算线程并串行执行。
静默合并、去重及频率限制控制发送量；普通内容被粗筛拒绝时不会调用 Qwen。
未配置粗筛路径时显式选择原有 Qwen-only 模式，仍不能保证一次回答只发一个请求。

## 配置与失败语义

根目录 `.env` 可配置新增参数；未填写时使用下面的新功能默认值：

```dotenv
ANSWER_COMPLETION_MODEL=qwen-flash
ANSWER_COMPLETION_TIMEOUT_SECONDS=3
ANSWER_COMPLETION_GATE_PATH=/var/lib/ai-interviewer/models/answer-completion/v2
```

独立客户端使用已有 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`，与主面试模型独立。
`ANSWER_COMPLETION_GATE_PATH` 必须指向包含 `encoder.onnx`、`tokenizer.json`、`head.json`、
`manifest.json` 的训练产物目录；服务器安装既有后端依赖即可，无 PyTorch/训练框架。
校验 V2 特征契约、单条输入约定、文件摘要、384 维权重、阈值及验收标记后加载。
量化校准/评估和服务器均逐条编码，避免批量形态改变动态量化分数。缺失、篡改、未通过验收的
配置产物均报错，不绕过粗筛。空路径显式选择 Qwen-only 模式；替换模型后重启应用进程。
原有语音服务仍要求 `SPEECH_ENABLED=true` 及正确的 `SPEECH_REGION`；模型 HTTP 地址和
语音区域分别按各自原配置验证。修改代码或环境配置后重启后端、刷新网页。

Qwen API 有覆盖整个异步请求的 3 秒默认期限，SDK 重试为 0；不切换模型，不以本地逻辑
替代失败的模型判断。初始化失败返回 `completion_configuration_error`，推理、超时或
输出格式失败返回 `completion_detection_failed`，终止当前采集并显示错误。主面试连接
不会因此自动提交或进入下一题。日志记录模型、耗时、字符数、撤销状态及错误类型，
不记录回答正文、录音、凭据或密钥。
粗筛日志另记录分数、阈值、是否送入 Qwen 和耗时，不记录文本。

## MCP 自定义 WebSocket 传输

后端采用当前 `/ws/agent/` 上的**自定义 WebSocket 传输**，不是独立的 Streamable HTTP
端点。连接继承现有同源、用户认证和容量控制。`hello.capabilities` 新增
`answer_completion_mcp`；网页先完成 MCP 握手，再发送原有 `start`。

支持协议版本 `2025-06-18` 的 `initialize`、`notifications/initialized`、`ping`、
`tools/list` 和 `tools/call`。只有一个固定工具；不支持工具列表分页。工具调用 ID 必须
使用规范小写 UUID 字符串，复用现有数据库幂等控制。

```json
{
  "jsonrpc": "2.0",
  "id": "<新的规范UUID>",
  "method": "tools/call",
  "params": {
    "name": "finish_current_answer",
    "arguments": {
      "question_id": "<当前问题ID>",
      "answer_text": "<完整最终转写>",
      "completion_receipt": "<后端STT final事件中的签名凭据>",
      "progress_events": true
    }
  }
}
```

独立 STT 连接通过下面的命令启用观察器；旧 `{"type":"start"}` 仍保留原协议：

```json
{"type":"start","completion_detection":true,"question_id":"<当前问题ID>"}
```

静默确认事件为 `{"type":"answer_completion","question_id":"..."}`。它只要求浏览器
flush PCM 并发送原有 `stop`，不含提交凭据。确认有效的 `final` 才附加
`completion_receipt`。凭据有效期 60 秒，绑定认证用户、问题 ID 和完整转写摘要。
更改回答、伪造、过期及跨用户凭据会在模型调用前被拒绝；当前题、busy 和重复请求仍
经过原协议检查。凭据没有跨进程内存依赖。

自定义传输保留绑定请求 UUID 的 `started/progress/assessment` 事件。
工具成功终态使用 JSON-RPC `result`，其中 `structuredContent` 是原有 `question/finished`
响应，`content` 提供相同正文的 JSON 文本。先按原有安全凭据保存原正文，再包装发送，
没有改动安全审查或历史记录摘要。协议/业务错误通过 JSON-RPC `error` 返回。

## 已验证范围

离线测试覆盖任意措辞的发送资格、0.5 秒稳定窗口、连续转写合并、启动频率限制、
否定结果、三秒实际等待、文字/语音补充、迟到结果、重复回调、单请求并发、超时、
收尾文本变化、凭据绑定/过期、MCP 进入真实 Agent 核心及重复/旧题拒绝。
前端测试运行真实协调器和 MCP 握手处理器，设备、网络与模型使用显式替身。

2026-10-03 取消本地话术筛选后，使用虚构文本直接检查真实供应商 API。
最初的提示将普通英文技术描述和中文项目总结误判为结束；随后明确话轮结束语义，
增加固定中英反例。最终一轮 12 个样例全部进入 Qwen Flash，结果符合样例标签，
覆盖自然结束表达、普通内容、总结、否定、引用及继续补充；耗时约 0.61–2.06 秒。
这些样例仅为提示修订后的冒烟检查，包含此前的误判样例，不是独立准确率评测，
也不证明真实麦克风、ASR 或完整语音面试的端到端体验。

2026-10-03 生产配置已启用 V2 本地模型与北京语音端点，部署探针实际校验了
`finish_current_answer` 注册。虚构英文 TTS 生成 8.88 秒音频，按浏览器格式发送到真实
HTTPS STT，收到 17 次转写事件；最后一次转写更新后 3.008 秒返回自动结束通知，
收尾 final 附带凭据，已验证用户/问题/最终文本的签名绑定，无供应商错误。
从 PCM 发送结束到通知耗时 7.895 秒，包含 ASR 定稿延迟，不能描述为音频停止后
恰好三秒完成。原始[生产合成语音记录](../../training/answer_completion/results/v2/production-synthetic-speech-completion.json)
只证明这条虚构音频链路；真人麦克风、噪声与完整数字人体验仍未验收。

最终在一场临时虚构面试中重复该完整链路，保持供应商配置与期限：最后一次转写后
3.025 秒确认结束，真实凭据经 `finish_current_answer` 提交；原有安全检查、回答保存、
评分和下一题生成均完成，数据库保存的回答与既有首尾空白处理契约一致。
临时账号、会话及关联面试记录已清理。[最终 MCP 联调记录](../../training/answer_completion/results/v2/production-synthetic-speech-mcp-final.json)
和前次握手/脚本诊断记录均保留，不将合成单例作为准确率或真人设备验收。

后续按用户要求增加了[12 个合成音频用例](../../training/answer_completion/results/synthetic-audio-v1/README.md)：
中英文直接/间接结束、否定、引用、后文继续，以及一秒内补充和通知后收尾补充，
均经真实 TTS/ASR 符合预先固定的通知/凭据预期。四个单段结束用例的转写后静默为
3.004–3.035 秒，PCM 停发到通知为 7.016–7.948 秒；后者仍包含 ASR 延迟。
一秒内补充没有误提交，通知后补充撤销最终凭据，首例 MCP 成功保存评分并返回下一题。
生产代码、模型和阈值未改动，仍不代表真人/噪声或独立准确率验收。

参考：[百炼思考模式设置](https://help.aliyun.com/en/model-studio/deep-thinking)、
[MCP 工具消息](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)、
[MCP 自定义传输](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports)。
