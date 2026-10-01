# PDF 隔离执行与服务容量

生产 PDF 上传现在必须经过 Linux bubblewrap 沙箱。Windows 后端通过 WSL 调用，
Linux 后端直接调用同一监督器。未配置或不可用时明确失败，不在后端线程中备用解析。
传统提取算法、页面尺寸、视觉模型和 Agent 校对契约保持原样。

## 运行环境

本机已配置 Ubuntu-24.04、Python 3.12.3、bubblewrap 0.9.0；解析依赖固定在
[requirements.txt](requirements.txt)。沙箱运行目录是用户目录中的独立依赖目录，
无需把整个项目、虚拟环境或模型凭据复制进去。

下面命令在 Ubuntu 24.04 内执行，前提是 `/usr/bin/python3`、`curl`、`libseccomp.so.2`
已可用，且系统允许非特权用户创建 user/network namespace。其他发行版需显式适配环境；
配置不满足时不得关闭隔离或添加本机解析回退。

```bash
runtime="$HOME/.local/share/ai-interviewer/pdf-sandbox"
mkdir -p "$runtime/downloads" "$runtime/tools" "$runtime/lib"
cd "$runtime/downloads"
apt download bubblewrap=0.9.0-1ubuntu0.1
dpkg-deb -x bubblewrap_0.9.0-1ubuntu0.1_amd64.deb "$runtime/tools"
curl --fail --location https://bootstrap.pypa.io/pip/pip.pyz --output pip.pyz
# 将 /path/to/backend 替换为 Linux 路径；WSL 可用 /mnt/d/.../backend。
/usr/bin/python3 pip.pyz install --only-binary=:all: --target "$runtime/lib" \
  -r /path/to/backend/sandbox/requirements.txt
"$runtime/tools/usr/bin/bwrap" --version
```

在后端 `.env` 中配置实际 Linux 用户目录：

```dotenv
PDF_SANDBOX_DISTRO=Ubuntu-24.04
PDF_SANDBOX_RUNTIME=/home/your-user/.local/share/ai-interviewer/pdf-sandbox
PDF_SANDBOX_MEMORY_MIB=768
PDF_SANDBOX_CPU_SECONDS=20
PDF_SANDBOX_WALL_SECONDS=30
SERVICE_AGENT_CONNECTIONS=4
SERVICE_PDF_UPLOADS=2
```

Linux 本机运行可省略 `PDF_SANDBOX_DISTRO`；Windows 源码必须位于 WSL 可访问的本地盘。
此实现依赖 Ubuntu 的 `/usr` 布局，不支持直接把其他系统路径套用为运行目录。
修改配置后重启 `python -m uvicorn config.asgi:application --host 127.0.0.1 --port 8765 --ws websockets-sansio`。

## 具体边界

| 边界 | 行为 |
| --- | --- |
| 文件与网络 | 只读挂载系统 `/usr`、隔离依赖和三个必要源码文件，空网络命名空间、空环境；不挂载 `/mnt`、用户目录、项目目录或 `.env` |
| 系统调用 | seccomp 拒绝创建 socket、派生/替换进程、修改命名空间、ptrace 等；任何限制安装失败都退出 |
| PDF 资源 | 新增每个工作进程 768 MiB 地址空间、20 CPU 秒、30 秒墙钟；文件写入上限 0，核心转储关闭，最多 64 个文件描述符 |
| 结果通道 | 监督器和父进程各自最多读取 160 MiB；父进程校验字段、页序、文字长度、base64 和 PNG 头/尺寸，不解码图像 |
| 输入 | 文件原有上限仍为 10 MiB、10 页、每页原文 30000 字符、图像最长边 1800；ASGI 额外限制正文为 10 MiB + 64 KiB multipart 开销 |
| PDF 并发 | 同机最多 2 个上传请求，包括上传、提取和视觉阶段；每份文档原有视觉并发 3 保持不变 |
| 面试容量 | 同机最多 4 个 Agent WebSocket，包括空闲连接；断线后仍在途的同步 SDK 调用继续占用名额，直到实际返回 |
| 满额 | PDF 在读取正文前返回 503，Agent 在握手前拒绝；不排队、不重试、不触发业务 fallback |
| 超时与取消 | 监督器终止并等待工作进程组；父进程总通信截止时间为墙钟限额加 10 秒，另有 5 秒清理等待，异常会记录 |

容量通过内核文件锁跨进程共享。所有同机 worker 必须使用相同的限额和
`SERVICE_CAPACITY_DIR`（默认 `backend/.capacity`）。服务运行时不可删除这些锁文件；
进程崩溃后内核自动解锁。测试使用独立临时目录，不能占用生产名额。

必须经过 `config.asgi:application`；绕过入口直接调用 Django view 不会获得上传前准入。
握手前发送 WebSocket close 在 Uvicorn 表现为 HTTP 拒绝，浏览器不一定能看到 1013 关闭码。
HTTP 流已开始后的 PDF 失败仍以 NDJSON `error` 结束，HTTP 200 不能当作成功。

这些是同机并发及解析资源限制，不是跨主机配额、每分钟速率或 token 费用预算。
文件锁本身不防公网慢速占用连接；服务仍遵循原有仅本机访问策略。
取消远端模型请求不能保证供应商停止执行或计费。沙箱共享宿主 Linux 内核，未声称已完成
所有恶意 PDF 的渗透测试；生产部署还需按目标环境验证内核、依赖与外围访问策略。

## 验证

在 backend 目录使用后端 Python：

```text
python manage.py test interviews.tests.test_resources interviews.tests.test_agent.ProviderTests
python tests/run_pdf_sandbox.py
```

第一条不依赖 WSL 或真实模型，覆盖真实跨进程锁、进程死亡释放、请求取消、在途模型借用、
上传前拒绝和实际 Django 对无长度/伪报长度分片的 413。沙箱返回边界在此显式模拟。
第二条必须使用真实沙箱，验证两页合成 PDF 与原提取算法一致、损坏/加密/超页拒绝，
以及文件/网络/派生进程隔离、内存限制、受控超时、异常退出及取消清理；不调用付费模型。
取消用例先在真实 Linux 进程表观察本次独立沙箱，再取消调用，并确认沙箱及监督器退出。

隔离命令依据：[bubblewrap 官方说明](https://github.com/containers/bubblewrap)。
