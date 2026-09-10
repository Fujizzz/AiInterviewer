# app 与 tests 文件说明

本文覆盖两个目录下全部 16 个 Python 源码文件。`__pycache__` 是 Python 自动生成的缓存，不属于源码。

## 整体流程

入口 `main.py` 读取简历文本，然后启动 `app/graph.py` 构建的 LangGraph。执行顺序为：解析简历 → 生成问题 → 暂停等待回答 → 分析回答 → 保存历史 → 决策。追问、换主题都回到提问节点，结束则进入最终评价节点。

四个 LLM 模块分别负责解析、提问、分析和评价；次数限制和路由由 Python 代码控制。节点返回需要更新的字段，LangGraph 将其合并到共享状态中。

## app：应用代码

### `app/__init__.py`

标识应用的 Python 包，使其他文件可以通过 `from app.graph import build_graph` 等方式导入模块。文件本身不启动面试，也不创建模型连接。

### `app/state.py`

使用 `TypedDict(total=False)` 定义共享状态 `InterviewState`，允许各阶段只包含已经生成的字段。字段涵盖简历、候选人信息、主题列表与下标、当前问答、分析、历史、追问次数、题数限制、决策及最终报告。

它主要提供字段类型说明，不执行运行时校验。模型输出的结构校验由各模块的 Pydantic 模型完成。

### `app/resume.py`

将文件转换为文本，与后续 LLM 理解简历的步骤分开。`read_resume()` 对 PDF 使用 `pypdf.PdfReader` 按页提取文字并拼接；其他文件按 `utf-8-sig` 读取，兼容 UTF-8 BOM。

函数拒绝加密 PDF、无可提取文字的 PDF 和空内容，并把 PDF 解析错误转换为清晰的异常提示。没有 OCR 功能，扫描件需要先在外部转换。

### `app/llm.py`

为四个 LLM 模块提供统一的结构化调用接口。

- `OutputModel` 是公共 Pydantic 基类，禁止额外字段并清理字符串首尾空白。
- `StructuredLLM` 使用 `Protocol` 定义调用约定：传入提示词、数据字典和输出类型，返回已校验对象。真实模型和离线替身遵循同一接口。
- `OpenAILLM.__init__()` 加载项目 `.env`，读取供应商、模型、密钥和温度，创建 SDK 客户端。系统已有环境变量优先；千问使用独立的兼容接口地址。
- `__call__()` 选择供应商。OpenAI 使用 Responses API 的 `parse()`，以 `text_format` 指定输出类型；API 异常转换为 `LLMError`，错误消息不打印密钥或完整请求。
- `_qwen()` 使用 Chat Completions JSON Object 模式并关闭思考，将 JSON Schema 放入提示词，再通过 `model_validate_json()` 校验返回内容。结构无效时再尝试一次；拒绝、截断、空响应则报错。

SDK 的请求重试与千问的结构校验重试是两个层次。虽然类名是 `OpenAILLM`，它同时负责 OpenAI 和千问，因为两条调用路径都使用 OpenAI SDK。

### `app/graph.py`

构建面试图并实现确定性的流程控制。

- `ask_candidate()` 调用 `interrupt()` 暂停并返回问题。CLI 用相同 `thread_id` 和 `Command(resume=...)` 恢复后，校验回答并写入状态。
- `save_history()` 将主题、问题、回答及分析组成一条记录，用新列表追加到历史，避免原地修改旧列表。
- `decide_next_step()` 先检查总题数，再判断是否允许追问，最后检查是否还有主题，返回 `finish`、`follow_up` 或 `next_topic`。
- `decision()` 更新追问次数或主题下标；换主题时清零追问次数。
- `build_graph()` 使用 `StateGraph(InterviewState)` 注册节点，通过 `partial` 注入模型，连接普通边和条件边，最后编译图。

检查点由 `InMemorySaver` 保存在内存中，不写数据库。不同 `thread_id` 隔离面试，关闭进程后无法恢复。测试可以向 `build_graph()` 注入替身，执行同一张图而不请求 API。

### `app/agents/__init__.py`

标识四个 LLM 模块所在的子包。只负责模块组织，不增加新的代理或运行流程。

### `app/agents/resume_parser.py`

提取候选人信息与面试主题。`CandidateProfile` 规定姓名、技能、经历和项目字段；`ResumeOutput` 组合候选人信息和主题列表。

`parse_resume()` 先校验简历及题数配置，再请求模型只提取有依据的事实，并尽量将同一项目的技术细节合并为一个主题。代码去除空主题，按原顺序消除完全相同的主题，没有主题时抛出错误。成功后初始化主题下标、追问次数、空历史、默认限制和未结束标志。

### `app/agents/question_agent.py`

根据候选人信息、当前主题、历史问题、上一轮回答分析和追问次数提问。`QuestionOutput` 要求问题是非空字符串。

`generate_question()` 在新主题时要求开场问题，追问时提供上一轮分析。提示词要求简短、只问一个方面、不提供答案选项。返回后，代码去除空白并忽略大小写，与历史问题比较；完全重复时再生成一次，仍重复则报错。

语义改写重复、问题长度和提示性选项目前主要依靠提示词约束，并非全部由硬性代码校验。

### `app/agents/answer_analyzer.py`

分析单轮回答，提供后续提问所需的证据，不生成最终分数。`AnswerAnalysis` 包含摘要、具体程度、技术深度、证据强度、缺失信息和追问建议；程度字段通过 `Literal` 限定为 `low`、`medium`、`high`。

`analyze_answer()` 只传入当前问题、回答与主题，将校验后的结果转成字典写入 `answer_analysis`。路由器结合建议与次数限制确定下一步。

### `app/agents/evaluator.py`

生成最终面试评价。`FinalReport` 包含总分、技术深度、问题解决、沟通表达、优点、待改进项和总结，使用 Pydantic 将分数限制为 1–5。

`evaluate_interview()` 只传入问答历史，要求模型依据回答证据评分、说明未考察领域，不因简历关键词加分。成功后返回报告并设置 `interview_finished=True`。当前总分由模型给出，不是程序对分项进行加权计算。

## tests：测试代码

### `tests/__init__.py`

标识测试包，支持导入 `tests.fixtures`，也支持 `python -m tests.smoke_interview` 这样的模块启动方式。文件本身不执行测试。

### `tests/fixtures.py`

提供离线模型替身 `FixtureLLM`。`__init__()` 设置是否建议追问并初始化调用记录；`__call__()` 根据请求的 Pydantic 类型返回固定的简历信息、问题、回答分析或评价。

问题随主题和追问次数变化，分析包含输入回答，所有结果仍通过 `schema.model_validate()` 校验。调用记录用于检查节点执行次数和输入。固定分数仅是测试数据，不能评价真实候选人。

### `tests/test_interview.py`

使用标准库 `unittest`、真实 LangGraph 和 `FixtureLLM` 检查核心行为，无需真实 API Key。

覆盖五轮中断恢复、追问和换主题计数、历史保存、最终结束、题数限制、主题耗尽、线程隔离、CLI 空回答重试和 JSON 输出、非法初始输入、无主题简历以及重复问题。嵌套函数 `no_topics()` 和 `duplicate()` 分别构造无主题和重复提问场景。

OpenAI 包装器测试使用 `unittest.mock.patch` 替换环境配置和 SDK 客户端，检查结构化参数及没有解析结果时的报错，不发送网络请求。

### `tests/test_qwen_pdf.py`

包含千问与简历读取两组测试。辅助函数 `response()` 用 `SimpleNamespace` 模拟 Chat Completions 响应。

`QwenTests` 模拟 SDK 返回值，检查接口与模型配置、JSON 模式、关闭思考、结构失败后重试、两次无效后停止和截断输出拒绝。`ResumeTests` 在临时目录创建带 BOM 的中文文本和真实空白 PDF，检查编码和空内容错误；页顺序测试模拟 PDF 页面，验证按页拼接。测试不依赖用户的私人简历。

### `tests/smoke_interview.py`

提供离线端到端演示。`main()` 准备五条回答，内部 `read_answer()` 从迭代器逐条取出并打印，替代键盘输入。

脚本将 `FixtureLLM` 注入真实图，调用 `main.py` 的 `run_interview()`，走过提问、中断恢复、分析和报告输出。最后断言完成五轮且涉及两个主题。它验证流程连接，不验证真实模型质量。

### `tests/live_interview.py`

提供需要主动运行的真实 API 测试入口，不会被 `unittest discover` 自动执行。

`main()` 从命令行取得简历路径，使用实际模型配置运行面试，在终端接收模拟回答。完成后检查回答数量为 3–5，并将候选人信息、主题、问答历史和最终报告写入 `output/live_interview_test.json`。

记录包含 `test_only=True` 和模拟测试说明，不是简历所有者的正式评价。该脚本会实际调用模型；输出目录被 Git 忽略，同名记录在下次成功运行时覆盖。
