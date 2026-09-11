# 测试说明

验证日期：2026-09-11。本机 django_env，Python 3.12.14；Node.js 24.19.0；Codex 内置 Chromium 浏览器。

## 可复现命令

在 backend 目录、激活 django_env 后：

```powershell
$env:DJANGO_SECRET_KEY = python -s -c "import secrets; print(secrets.token_urlsafe(48))"
python -s manage.py test interviews
python -s manage.py makemigrations --check --dry-run
python -s tools/check_docs.py
python -s tests/run_e2e.py
```

Django 测试使用隔离数据库；`run_e2e.py` 创建临时 SQLite 文件和临时本机端口，迁移后启动实际 Uvicorn 服务，退出时停止该子进程并清理临时文件。
不会改写开发数据库，不依赖真实摄像头、麦克风或云端服务。Node.js 22+ 必须可通过 PATH 找到。

## 已验证结果

| 层次 | 结果与范围 |
| --- | --- |
| Django / ASGI 自动化 | 20 项通过：业务接口与协议边界，新增 100/101 题边界、显式子集和拆分资源检查；流式测试使用禁止数据库访问的 SimpleTestCase，并在禁止文件打开和 SQLite 连接时完成回传 |
| 前端客户端自动化 | 5 项通过：损坏回传、错误 ACK、最终计数不匹配、超时和主动取消 |
| 真实 HTTP | 场次创建→开始→提交答案→结束→查询成功；并发相同版本的两次更新分别返回 200、409 |
| WAV 音频 | 8 个分片 / 32,044 bytes，内存中原样回传，重新解码为 16,000 音频帧 |
| 实际前端 StreamClient | 两个并发连接，各 6 个不同尺寸分片 / 1,131,008 bytes，逐字节一致；进度在关闭前到达 |
| 不落库验证 | 实际 WAV 和并发前端流式测试前后，SQLite 文件逐字节完全相同 |
| 浏览器 ping/pong | 收到匹配响应，单次实测 RTT 5.7 ms |
| 浏览器二进制 | 12 个分片 / 393,216 bytes，全部通过 SHA-256 |
| 浏览器合成音视频 | 约 3 秒，11 个分片 / 96,893 bytes，通过 SHA-256；回传 WebM 解码为 640×360，readyState=4 |
| 浏览器清空结果 | 回放 src 为空、readyState=0、字节和分片计数归零、页面日志为空 |
| 注释覆盖 | 28 个 Python 模块、95 个类/函数、6 个 JavaScript 模块，缺失 0 |
| 迁移与静态检查 | 无模型迁移变化；整个仓库 Ruff 通过；重构入口 JavaScript 语法检查通过 |
| 既有 Agent MVP | 根目录独立环境：79 项测试、5 个子测试通过；未修改 Agent 实现、策略和依赖 |

根目录检查使用 `uv sync --extra dev --locked` 后的环境，运行 `uv run pytest -q` 与 `uv run ruff check .`。

以上时延和分片数是一次本机验证的观测值，不是性能保证。MediaRecorder 调度不精确，不使用分片数量推算录制时长。

## 浏览器复验

1. 按 README 启动服务，打开 http://127.0.0.1:8765/。
2. 点击 Ping / Pong，检查匹配响应及 PASS。
3. 点击二进制分片测试，确认结束前计数增长，最终 12 个分片、393216 bytes。
4. 点击合成音视频测试，等待录制结束，确认 PASS；点击回传播放器验证媒体播放。
5. 点击“清空结果与媒体缓存”，确认回放源、统计与页面日志清空；刷新后没有历史结果。

真实麦克风/摄像头入口已实现，但本次没有请求真实设备权限；硬件质量、权限拒绝界面和跨浏览器编码兼容性需要在目标设备进一步验证。
浏览器测试证明本地音视频分片可双向回传并重组；不代表 WebRTC、低延迟远端播放、生产并发负载或公网网络已经验证。
