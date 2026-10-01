# Backend 后端、MVP Agent 与流式传输测试

Django + DRF 提供题库、练习场次、单题记录接口，SQLite 仅保存业务数据。流式测试的媒体与统计只在内存中处理，不写数据库或本地文件。
同一 ASGI 服务提供 WebSocket 回传接口与浏览器测试页面，用于验证 ping/pong、二进制和音视频分片传输。
本目录已接入根目录的 Agent MVP，通过 `/ws/agent/` 提供简历文本解析、逐题面试、评价与最终报告，测试页面位于 `/agent/`。
Agent 沿用 MVP 的默认参数、策略和每题 120 秒逻辑预算；后端练习接口继续保留原有 10 秒准备和 90 秒回答配置，两条流程独立运行。

## Coding Agent 必须遵循的开发原则

所有 Coding Agent 在新增、修改、重构或删除 `backend/` 内代码时，必须遵循以下要求：

1. **学术风格的实现注释**：在函数、方法及关键代码块处提供准确、严谨、可核验的注释，说明功能、输入与输出、实现逻辑、设计依据和适用约束；涉及状态转换、边界条件、异常或副作用时，应说明其处理方式。注释应解释实现原因与逻辑关系，避免仅复述代码，也不得编造学术引用或未经验证的结论。
2. **文件顶部的功能说明与目录**：每个代码文件顶部必须说明文件职责、主要实现逻辑及与相关模块的关系，并列出文件中实际实现的函数、类与关键方法，以及关键变量、常量和配置项的名称与用途，供 Coding Agent 和开发者快速定位。目录应与当前实现一致，不保留已删除或重命名的条目。
3. **代码与注释同步原子修改**：代码实现、对应注释和文件顶部目录必须作为同一逻辑变更单元同步更新、检查和交付；如提交代码，必须纳入同一次提交。修改函数签名、行为、数据流、关键变量或模块职责时，必须同时修订受影响的说明；删除或替换实现时，必须同步清理失效注释及目录引用。不得先交付代码，再以“后续补充”为由延迟更新注释。
4. **完成前检查一致性**：交付前逐项核对变更涉及的注释和目录，确认其准确反映实际行为，并运行 `python tools/check_docs.py` 检查声明注释、目录及模块变量索引。修改检查器时还须运行 `python -m unittest discover -s tools -p "test_*.py"`。检查器对 Python 定义和 JavaScript 函数（含匿名回调）、类分别要求声明处注释与顶部关联条目，并反向检查残留条目。自动检查不能替代对功能说明、实现逻辑、关键状态与代码一致性的人工核对，也不能证明 Git 提交原子性。

具体注释格式及模块职责参见 [代码阅读指南](docs/code-guide.md)。

## 技术栈与环境

| 技术 | 已验证版本 | 用途 |
| --- | --- | --- |
| Python | 3.12.14 | 后端运行环境 |
| Django | 5.2.17 | ORM、迁移、HTTP 路由 |
| Django REST Framework | 3.18.1 | JSON 接口、序列化、参数校验 |
| SQLite | 3.53.4（本机） | 默认存储，文件 `backend/db.sqlite3` |
| Uvicorn | 0.52.4 | HTTP 与 WebSocket 的 ASGI 服务 |
| DashScope | 1.27.7 | 百炼新加坡 Qwen realtime TTS / Qwen-Audio streaming STT |
| websockets | 16.1.1 | WebSocket 协议支持，本次补充安装 |
| 浏览器原生 API | WebSocket / MediaRecorder / Web Audio / Canvas | 诊断页面和 PCM 采集；数字人播放器另有 npm 构建 |

后端 Python 直接依赖固定在本目录的 `requirements.txt`，使用 `python -m pip install -r requirements.txt` 安装即可，无需指定环境管理工具。可按个人习惯使用 Python 自带的 `venv` 或已有 Python 环境；以下命令中的 `python` 应指向你选择的解释器。
本目录 `requirements.txt` 通过 `-r ../requirements.txt` 引用已有 MVP 依赖，安装一次即可运行后端与 Agent；保留完整仓库目录。根目录的 `pyproject.toml` 与 `uv.lock` 继续管理终端 MVP 环境。
本次使用 SQLite，没有添加 MySQL 或 PostgreSQL 的备用连接配置。

## 启动

在 PowerShell 中进入本目录，然后运行：

```powershell
python -m pip install -r requirements.txt
python -m pip check
$env:DJANGO_SECRET_KEY = python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py migrate
python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

打开 [流式测试页面](http://127.0.0.1:8765/) 或 [健康检查](http://127.0.0.1:8765/api/health/)。
测试 AI 面试请打开 [MVP Agent 测试页](http://127.0.0.1:8765/agent/)，并先按下节配置模型密钥。
必须使用 ASGI 启动命令；`manage.py runserver` 不能提供这里的 WebSocket 路由。
`DJANGO_SECRET_KEY` 必须在环境变量或仓库根目录 `.env` 中显式设置，上面的命令仅为当前开发 shell 生成随机值，源码不包含应用密钥。
可显式设置 `INTERVIEW_DB_PATH` 指向其他 SQLite 文件；默认数据库及本地秘密文件已加入 `.gitignore`。
迁移会创建数据表，并初始化原有两道通用练习题，已有题目不会被覆盖。

## Agent 模型配置

API key 存放在 **仓库根目录 `.env`**，与终端 MVP 共用配置。在 `backend/` 目录首次配置可执行 `Copy-Item ../.env.example ../.env`；已有文件请直接编辑，避免覆盖。
OpenAI 填写 `LLM_PROVIDER=openai`、`OPENAI_API_KEY`、`OPENAI_MODEL`；千问填写 `LLM_PROVIDER=dashscope`、`DASHSCOPE_API_KEY`、`DASHSCOPE_MODEL`。
后端自动读取该文件，进程环境变量优先；修改后重启。`.env` 已被 Git 忽略，模板不含密钥。
完整配置示例、网络协议与取消限制见 [MVP Agent 接入说明](docs/agent-integration.md)。

## 数字人语音面试

`/agent/` 已加入数字人播放器、TTS 朗读、按钮控制的英文 STT 和可修改转录。
新增 `/api/speech/tts/`、`/api/speech/audio/<uuid>/`、`/ws/speech/stt/`；问题生成与评价仍沿用原 Agent。
百炼语音默认关闭，配置项见根目录 [`.env.example`](../.env.example)。密钥只由服务端读取，语音使用新加坡业务空间的原生 SDK 地址。
完整的 UE 角色组装、串流、启动和验收步骤见 [数字人操作说明](../docs/guides/DIGITAL_HUMAN_SETUP.md)。
角色已经组装并在浏览器中显示，云端英文 TTS/STT 短句调用已实测通过。
独立诊断页 `/stream-demo/digital-human-check.html` 可在不调用面试 LLM 的情况下检查数字人朗读和测试音频转录。
当前前端修改统一放在 `backend/frontend/`，数字人播放器源码、构建配置和测试位于其 `digital-human/` 子目录。
在仓库根目录执行 `pnpm --dir backend/frontend/digital-human run build` 构建播放器，
执行 `pnpm --dir backend/frontend/digital-human test` 运行语音前端测试；首次使用先按前端维护说明安装依赖。
目录与开发方式见 [前端维护说明](frontend/README.md)，后续由后端团队迁入根目录 `frontend/`。

## 流式测试

1. **Ping / Pong**：确认匹配的消息 ID，显示往返延迟。
2. **二进制分片测试**：12 个 32 KiB 分片依次发送，页面在连接结束前实时更新校验计数。
3. **合成音视频测试**：画布视频 + 合成音调，录制约 3 秒 WebM，经 MediaRecorder 分片回传；结束后可播放回传数据组成的媒体。
4. **真实设备测试**：点击麦克风或摄像头按钮并由使用者授权，手动停止或 30 秒后停止。

前端按发送顺序处理 Blob 转换、SHA-256 和回传，等待最后一个分片校验完成才发送 finish。
编码不支持、权限拒绝、超时、断线、过大分片或校验失败都会显示错误，不自动重连、重试、丢帧或降级格式。
录制目标分片间隔为 250 ms，浏览器不保证精确间隔。完成后播放用于验证重组可解码，尚未实现远端边录边播。

## 目录与文档

```text
backend/
  config/                 Django 设置、URL 与 ASGI 入口
  interviews/
    models.py             三张业务表
    services.py           场次事务和状态转换
    agent_provider.py     后端模型配置、脱敏日志与客户端释放
    agent_session.py      MVP 用例的逐轮网络适配，独立内存仓库
    agent_socket.py       文字面试命令、并发限制与连接生命周期
    access.py             HTTP/WebSocket 共用访问策略
    middleware.py         HTTP 请求拦截
    demo.py               测试页资源白名单
    api/                  REST 序列化、视图与路由
    streaming/            协议状态校验与 ASGI 连接管理
    speech/               百炼 TTS/STT、临时 WAV 缓存与原始 PCM 协议
    migrations/           Schema 与初始题目
    tests/                Django / ASGI 测试
  frontend/               app 调度、view 展示、media 采集、stream-client 协议
    digital-human/        官方 Pixel Streaming SDK 包装、pnpm 构建与语音前端测试
  diagnostics/            独立数字人语音验证页，不生成问题或评分
  docs/                   Schema、协议与测试说明
  tests/                  Node 客户端测试、真实服务器联调
  tools/check_docs.py     声明注释、符号目录和模块变量索引检查
  tools/javascript_docs.py Tree-sitter 语法树与声明处 JSDoc 关联
  tools/test_check_docs.py 检查器独立回归测试，不加载业务应用
  tools/test_docs_contract.py 双位置关联、语法边界及进程退出测试
  requirements-docs.txt   注释检查的固定开发依赖，服务运行不需要
```

查阅入口：[代码阅读指南](docs/code-guide.md)、[Schema](docs/schema.md)、[接口协议](docs/api.md)、[测试说明](docs/testing.md)。

## 测试

注释检查使用 Python AST 与 Tree-sitter JavaScript 语法树。在本目录先安装 `python -m pip install -r requirements-docs.txt`；这两项依赖只供开发检查，不影响服务运行。未安装解析器或源码解析失败会明确报错，不跳过检查，也不会回退到正则识别。

```powershell
# 当前 shell 先按启动步骤设置 DJANGO_SECRET_KEY。
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test interviews
python tools/check_docs.py
python -m unittest discover -s tools -p "test_*.py"

# 需要 Node.js 22+；使用临时 SQLite 和临时端口，不写入开发数据库。
python tests/run_e2e.py
python tests/run_agent_e2e.py  # 真实 ASGI + 离线模型替身，不调用收费模型
```

## 当前边界

- 本地单用户原型，HTTP 和 WebSocket 限制回环地址及同源访问，启动时只绑定 `127.0.0.1`。没有账号、权限隔离或公网部署。
- 音视频、字节数与校验统计只在内存中处理，不写数据库或媒体文件；不提供流式历史查询。
- 每次连接使用临时 connection_id，断开后服务端不保留结果。页面日志仅保留最近 30 行；服务端日志输出到控制台，启动时不要重定向到文件。
- 浏览器仅保留当前回放的临时 Blob URL，点击“清空结果与媒体缓存”、开始下一次测试或离开页面时释放。没有 localStorage、IndexedDB、下载或文件写入逻辑。
- verified_chunks 是客户端报告的校验数量，属于诊断指标，不是对恶意客户端的可信证明。
- Agent 已接入语音呈现；简历、答案和报告只保存在当前连接内存中。临时问题 WAV 最多保留十分钟，不写磁盘；用户音频不写数据库。UE WebRTC 串流由独立 Pixel Streaming 服务提供。文件上传、视频存储和 MySQL 适配尚未提供；评分与策略仍由根目录模块负责。
- Agent 当前返回完整问题和报告，没有逐 token 输出。沿用 MVP 的既有模型重试与问题/报告备用逻辑；断开连接不能保证已发送的同步模型请求在供应商处停止。

## 协议参考

- [MDN：MediaRecorder dataavailable](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event)：分片事件与末尾数据处理。
- [MDN：WebSocket binaryType](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/binaryType)：浏览器接收 ArrayBuffer。
- [Django：SQLite notes](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes)：SQLite 并发与事务限制。
