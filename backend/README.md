# Backend 后端与流式传输原型

Django + DRF 提供题库、练习场次、单题记录接口，SQLite 仅保存业务数据。流式测试的媒体与统计只在内存中处理，不写数据库或本地文件。
同一 ASGI 服务提供 WebSocket 回传接口与浏览器测试页面，用于验证 ping/pong、二进制和音视频分片传输。
本目录已迁入 [Fujizzz/AiInterviewer](https://github.com/Fujizzz/AiInterviewer)。根目录已有 Agent MVP；本次只整理独立后端，不改变其策略、参数或模型配置。后端练习接口继续保留原有 10 秒准备和 90 秒回答配置，尚未与 Agent 的计时契约合并。

## 技术栈与环境

| 技术 | 已验证版本 | 用途 |
| --- | --- | --- |
| Python | 3.12.14 | Conda 环境 `django_env` |
| Django | 5.2.17 | ORM、迁移、HTTP 路由 |
| Django REST Framework | 3.18.1 | JSON 接口、序列化、参数校验 |
| SQLite | 3.53.4（本机） | 默认存储，文件 `backend/db.sqlite3` |
| Uvicorn | 0.52.4 | HTTP 与 WebSocket 的 ASGI 服务 |
| websockets | 16.1.1 | WebSocket 协议支持，本次补充安装 |
| 浏览器原生 API | WebSocket / MediaRecorder / Web Audio / Canvas | 前端测试，无 npm 构建依赖 |

后端 Python 直接依赖固定在本目录的 `requirements.txt`；根目录的 `pyproject.toml` 与 `uv.lock` 管理 Agent MVP 的依赖，二者独立验证。
本机解释器为 `D:\my_files\conda_envs\django_env\python.exe`。
默认 Python 会混入用户目录的 site-packages，使用 `python -s` 隔离后 `pip check` 通过。
本次使用 SQLite，没有添加 MySQL 或 PostgreSQL 的备用连接配置。

## 启动

在 PowerShell 中进入本目录，然后运行：

```powershell
conda activate django_env
python -s -m pip install -r requirements.txt
python -s -m pip check
$env:DJANGO_SECRET_KEY = python -s -c "import secrets; print(secrets.token_urlsafe(48))"
python -s manage.py migrate
python -s -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio
```

打开 [流式测试页面](http://127.0.0.1:8765/) 或 [健康检查](http://127.0.0.1:8765/api/health/)。
必须使用 ASGI 启动命令；`manage.py runserver` 不能提供这里的 WebSocket 路由。
`DJANGO_SECRET_KEY` 必须显式设置，上面的命令仅为当前开发 shell 生成随机值，源码不包含应用密钥。
可显式设置 `INTERVIEW_DB_PATH` 指向其他 SQLite 文件；默认数据库及本地秘密文件已加入 `.gitignore`。
迁移会创建数据表，并初始化原有两道通用练习题，已有题目不会被覆盖。

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
    access.py             HTTP/WebSocket 共用访问策略
    middleware.py         HTTP 请求拦截
    demo.py               测试页资源白名单
    api/                  REST 序列化、视图与路由
    streaming/            协议状态校验与 ASGI 连接管理
    migrations/           Schema 与初始题目
    tests/                Django / ASGI 测试
  diagnostics/web/        后端流式诊断页，不是产品前端
  docs/                   Schema、协议与测试说明
  tests/                  Node 客户端测试、真实服务器联调
  tools/check_docs.py     文件目录与函数注释覆盖检查
```

查阅入口：[代码阅读指南](docs/code-guide.md)、[Schema](docs/schema.md)、[接口协议](docs/api.md)、[测试说明](docs/testing.md)。

## 测试

```powershell
# 当前 shell 先按启动步骤设置 DJANGO_SECRET_KEY。
python -s manage.py check
python -s manage.py makemigrations --check --dry-run
python -s manage.py test interviews
python -s tools/check_docs.py

# 需要 Node.js 22+；使用临时 SQLite 和临时端口，不写入开发数据库。
python -s tests/run_e2e.py
```

## 当前边界

- 本地单用户原型，HTTP 和 WebSocket 限制回环地址及同源访问，启动时只绑定 `127.0.0.1`。没有账号、权限隔离或公网部署。
- 音视频、字节数与校验统计只在内存中处理，不写数据库或媒体文件；不提供流式历史查询。
- 每次连接使用临时 connection_id，断开后服务端不保留结果。页面日志仅保留最近 30 行；服务端日志输出到控制台，启动时不要重定向到文件。
- 浏览器仅保留当前回放的临时 Blob URL，点击“清空结果与媒体缓存”、开始下一次测试或离开页面时释放。没有 localStorage、IndexedDB、下载或文件写入逻辑。
- verified_chunks 是客户端报告的校验数量，属于诊断指标，不是对恶意客户端的可信证明。
- 本目录尚未调用根目录的 Agent MVP，也未提供语音识别、视频存储、WebRTC 或 MySQL 适配；既有 Agent 评分与策略仍由根目录模块负责。
- 后续接口对接只调用 Agent 公共 Service 并复用 `shared/contracts/`；不要导入 Agent 内部策略或领域对象。

## 协议参考

- [MDN：MediaRecorder dataavailable](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder/dataavailable_event)：分片事件与末尾数据处理。
- [MDN：WebSocket binaryType](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket/binaryType)：浏览器接收 ArrayBuffer。
- [Django：SQLite notes](https://docs.djangoproject.com/en/5.2/ref/databases/#sqlite-notes)：SQLite 并发与事务限制。
