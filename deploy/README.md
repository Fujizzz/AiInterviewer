# 单机生产部署与维护

应用部署到 `https://47.239.50.129/`，文字面试入口为 `/agent/`。
主页公开；通过 `/register/` 注册、`/login/` 登录，每个账号的面试与练习记录独立，题库共用。
账号仅需非空用户名与密码，无邮箱、验证码或密码复杂度要求。

## 实际部署

| 项目 | 配置 |
| --- | --- |
| 系统 | Ubuntu 22.04.5 LTS |
| 应用解释器 | Python 3.12.14，独立安装，不替换系统 Python |
| 数据库 | PostgreSQL 14，数据库 `ai_interviewer`，应用角色 `aiinterviewer` |
| 应用服务 | `ai-interviewer.service`，低权限用户 `aiinterviewer`，仅监听 `127.0.0.1:8765` |
| 公网入口 | Nginx 80/443，HTTP 跳转 HTTPS，Django session 账号认证，WebSocket/NDJSON 不缓冲 |
| TLS | Let's Encrypt IP 短期证书，`ai-interviewer-certbot-renew.timer` 每六小时检查续签 |
| 发布目录 | `/opt/ai-interviewer/releases/<完整提交 SHA>`；`current` 符号链接指向当前版本 |
| 应用依赖 | 每个发布目录的 `.venv`；固定依赖保存在 `requirements-linux.lock.txt` |
| 凭据 | `/etc/ai-interviewer/app.env`，root 所有，权限 `0600`；模型配置沿用本地 |
| 用户账号 | PostgreSQL 中的 Django 用户与 session；密码仅保存哈希 |
| PDF 隔离环境 | `/opt/ai-interviewer/pdf-sandbox`，系统 Python 3.10 专用解析依赖和 bubblewrap |
| 容量锁 | `/var/lib/ai-interviewer/capacity`；保持原 4 个面试连接、2 个 PDF 上传限额 |
| 回答结束粗筛模型 | `/var/lib/ai-interviewer/models/answer-completion/v2-20261003`；离线 INT8，配置 `ANSWER_COMPLETION_GATE_PATH`，通过后仍需 Qwen |
| 生产语音 | `SPEECH_ENABLED=true`、`SPEECH_REGION=beijing`；沿用原 TTS/STT 模型、音频格式与超时 |
| 数据备份 | `backup-postgresql.sh` 手动创建 `/var/backups/ai-interviewer/*.dump` |

服务器使用新建数据库，只由既有迁移初始化两道练习题；未导入本地 SQLite 历史。
部署使用 Git 提交的源码归档；未上传 `.git`、本地数据库、日志、缓存或虚拟环境。
`.gitattributes` 的 `export-ignore` 将 `DigitalHuman/` 排除在后端发布归档之外；
UE 工程与 MetaHuman 资源仍保存在 Git/Git LFS，通过克隆仓库获取，在 GPU 机器上单独打包运行。
后端归档保留网页、语音服务及面试模块，并遵守压缩包 128 MiB、解压后 256 MiB 的发布上限。
Redis/Celery 接入不修改模型、采样参数、评分策略、推荐权重、PDF 参数或容量限制。

回答结束模型的 ZIP 来自私有 Kaggle V2 输出，单独上传到上述版本目录，未放入源码归档。
校验 ZIP 和 `manifest.json` 摘要、固定文件白名单、单条输入契约及 `release_approved`
后再启用；版本目录须允许应用用户读取，环境文件仍使用 root 私有权限。
服务器只需已有依赖锁中的 ONNX Runtime/tokenizers/NumPy，不安装 PyTorch 训练依赖。
部署探针校验模型真实加载/推理，并用临时认证会话访问线上 WebSocket，检查 MCP 握手
和 `finish_current_answer` 工具注册；它不调用结束工具、不录音，也不能证明意图准确率。
训练/推理及真实 Qwen 检查边界见[训练记录](../backend/docs/answer-completion-training.md)。
2026-10-03 已将功能提交 `6d3e2ca4b2cd8974502949931c89ec8b7aeb1f9a` 部署到生产，
模型单独校验后启用；HTTP、数据库、Redis、Celery、真实模型加载与 MCP 注册检查通过。
发现原语音开关关闭且默认新加坡端点与当前 key 区域不匹配；新加坡握手返回 401，
北京握手及真实合成/ASR 检查通过后，显式更新上述两个语音配置，并验证运行中进程已读取。
未增加自动区域切换或改变供应商模型/期限，修改前的私有环境备份保存在
`/etc/ai-interviewer/app.env.before-completion-20261003` 和
`/etc/ai-interviewer/app.env.before-speech-20261003`；原始文件权限保持 `0600`。
当前机器的 SSH 管理信息按用户要求保存在仓库根目录 `server_info.txt`，已加入 Git 忽略，
不进入 Git 提交、Kaggle 数据或源码发布归档。真实麦克风及完整设备体验仍须现场验证。

2026-10-05 已完成安全引擎连接保活专项更新：私有 `app.env` 显式配置
`AI_SECURITY_KEEPALIVE_SECONDS=60`，只替换安全传输模块并重启 ASGI；原模型、5 秒
审查门槛、SDK 零重试及其他服务配置不变。更新前的原传输文件和私有配置保存在
`/var/backups/ai-interviewer/security-network-20261005-181442/`，目录 0700、备份 0600。
当前整版本标识仍是 `c3d96fd617419f96ef0e44d2845b63ab1e710339`，专项文件哈希另存回执；
本地代码尚未提交推送，后续整版本发布必须包含本次修复，避免覆盖已生效的传输代码。
当前服务器的 324 次完整安全回归/稳定性/并发检查均通过原门槛，部署后 5 次实际安装
引擎检查均正确。数据、原失败记录边界及运行环境见
[安全引擎网络连接优化](../security_evaluation/network_optimization/README.md)。
本次同时合并远程 main 的 Plan and Execute 版本；面试时长和题数安全上限沿用该远程版本。
生产配置通过 `DJANGO_SETTINGS_MODULE=config.production` 显式启用；默认开发启动仍使用 SQLite。
PostgreSQL 配置不全或连接失败时明确报错，不回退到 SQLite。

## 维护命令

以下命令通过 SSH 在服务器以 root 执行：

```bash
systemctl status ai-interviewer ai-interviewer-celery redis-server nginx postgresql --no-pager
journalctl -u ai-interviewer -n 100 --no-pager
systemctl restart ai-interviewer
systemctl list-timers ai-interviewer-certbot-renew.timer --no-pager
bash /opt/ai-interviewer/current/deploy/backup-postgresql.sh
```

修改 `/etc/ai-interviewer/app.env` 后重启应用。不要把凭据文件放进发布包。
维护账号可在加载生产环境后使用 `manage.py changepassword 用户名`；不要直接写入明文密码。
应用退出后保留失败状态和日志，不自动重放面试或模型请求；排查后显式重启。
数据库备份尚未配置周期任务或异机保存；磁盘上既有归档不会自动删除。

### 个人岗位推荐来源

本次生产部署明确选用训练数据同源的 100 个实验岗位。ASGI 的 systemd 服务在私有
`app.env` 之后读取当前发布的 `deploy/recommendation.env`，仅覆盖
`RECOMMENDATION_JOB_CATALOG`，指向
`/opt/ai-interviewer/current/backend/interviews/recommendation/data/experience-jobs.json`。
此文件不含密钥，随发布记录并审查岗位来源；`current` 切换后路径自动对应新版本。
开发配置仍须显式选择来源，未配置/文件无效仍报错，不切换备用目录。

界面保留“非实时招聘职位”与实验模型标记；不更改粗排权重、特征单位、缺失处理或训练条件；个人中心按用户指定 20/5 进行两阶段推荐。
来源及转换记录见 [体验岗位说明](../backend/interviews/recommendation/data/README.md)。

发布验收使用临时账号、保存简历及数据库会话，向实际运行的 ASGI 发送带 Session/CSRF 的
推荐请求，要求完整目录 100 岗粗排、前 20 岗送入 API，最终返回 5 个唯一合法 ID、
双语简短理由及实验标记，并在成功或异常后清理临时账号、简历和会话。
验收会发起一次真实 LLM API 请求并产生服务商用量，复用私有 `app.env` 的既有文字模型
配置与超时；不重试、不增加等待、不使用粗排替代失败精排。HTTP/数据库/Redis/Celery
与岗位验收全部通过才记为部署成功。此检查验证功能接通，不证明真实招聘效果。

更新应用前先备份。把新源码解压到新的发布目录，在独立环境中验证锁定依赖与迁移，
停止应用后切换 `current` 并执行迁移，再启动和检查健康接口。
数据库 schema 变更的回滚必须单独审查迁移或恢复备份，不能仅靠切回源码。
更新期间不得删除共享容量锁；由服务完全停止后再进行需要清理运行状态的维护。

```bash
set -a
. /etc/ai-interviewer/app.env
set +a
cd /opt/ai-interviewer/current/backend
runuser -u aiinterviewer -- /opt/ai-interviewer/current/.venv/bin/python manage.py migrate --noinput
runuser -u aiinterviewer -- /opt/ai-interviewer/current/.venv/bin/python manage.py check --deploy
curl -fsS -H 'Host: 47.239.50.129' -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8765/login/ > /dev/null
```

首次重建服务器时，先安装 Nginx、PostgreSQL、bubblewrap、libseccomp2、libgomp1 和系统
Python 的 pip/venv；另装 Python 3.12.14 与独立应用虚拟环境，安装锁定依赖。
PDF 专用依赖须由 `/usr/bin/python3` 根据 `backend/sandbox/requirements.txt` 安装到
`/opt/ai-interviewer/pdf-sandbox/lib`；`tools/usr/bin/bwrap` 指向 `/usr/bin/bwrap`。
建立应用 Unix 用户、PostgreSQL 非超级用户与数据库；根据 `app.env.example` 填写独立随机
数据库密码、Django secret 和原模型配置。不得把系统 PostgreSQL 管理员凭据交给应用。

先配置只开放 `/.well-known/acme-challenge/` 的 80 端口 Nginx 站点，并在云安全组允许
TCP 80/443，随后申请证书：

```bash
/opt/ai-interviewer/certbot/bin/certbot certonly --non-interactive --agree-tos \
  --register-unsafely-without-email --preferred-profile shortlived \
  --webroot --webroot-path /var/www/ai-interviewer-acme \
  --ip-address 47.239.50.129 --cert-name 47.239.50.129
```

签发后安装 `nginx.conf` 和三个 systemd 文件。续签 service/timer 的实际文件名分别为
`ai-interviewer-certbot-renew.service`、`ai-interviewer-certbot-renew.timer`。
运行 `nginx -t`、`systemctl daemon-reload`，启用应用与续签 timer 后重载 Nginx。
禁止直接将 8765 或 PostgreSQL 端口开放到公网。80 需要持续保留 ACME 路径供续签。

## 已验证与边界

- 队列版本在本地 SQLite 和服务器临时 PostgreSQL 均通过 109 项后端测试，核心 211 项测试和前端 26 项测试通过。
- PostgreSQL migrations 与 `makemigrations --check --dry-run` 通过；健康接口报告 `postgresql`。
- 公网 HTTPS 主页与账号页公开；未登录 API 返回 401、面试页跳转登录，登录后可访问自己的记录。
- 注册、登录、退出、CSRF、跨账号读写隔离及退出后现有 WebSocket 失效均通过公网实测。
- 登录/注册页面通过真实浏览器中英文布局、登录跳转和退出检查；6 项 PDF 客户端测试通过。
- 公网 WSS 两个入口握手通过，并通过真实千问模型完成一题合成面试、评价与报告。
- 真实 PDF 沙箱通过文件/环境/网络/派生进程隔离、内存限制、超时、异常和取消测试。
- 实际 systemd 服务下完成合成 PDF 上传、隔离提取、视觉校对和最终 NDJSON result。
- IP 证书签发与 Certbot 续期 dry-run（含 Nginx 重载钩子）成功。
- 合成面试测试记录已按其唯一 ID 清理；初始数据库备份已实际恢复到临时库验证，随后删除临时库。
- 修改的 Python 文件通过 Ruff、原检查器的 Python 声明/目录校验，并已人工核对注释。
- 项目全量 `python tools/check_docs.py` 在本地出现访问违规，在服务器退出 139；未修改
  检查器或降低校验标准，不能声称全量注释自动检查通过。
- `check --deploy` 保留 IP 部署不启用子域 HSTS 或 preload 的两项警告；CSRF 保护已启用。
- 账号使用 Django 密码哈希与安全 session Cookie；HTTP 写请求含 CSRF 校验，WebSocket 验证 session 归属。
- 历史空归属记录不会分配给新账号；生产启用账号迁移前没有面试或练习记录。
- 未进行并发负载测试、真实浏览器设备授权测试或重启整台主机的演练。
- 本次功能代码、迁移、测试、对应注释与部署文档纳入同一次 Git 提交；全量注释检查仍受上述崩溃阻碍。

ASGI 下的数据库连接配置依据 [Django 5.2 数据库说明](https://docs.djangoproject.com/en/5.2/ref/databases/)。
IP 证书与续签设置依据 [Let's Encrypt / Certbot 官方说明](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)。

## Redis 与 Celery

生产的 PDF 上传由独立 `ai-interviewer-celery.service` 执行，页面仍使用同一条 NDJSON
连接接收排队、提取、校对和完成事件；取消/关闭连接通过 Redis 消费标记通知 worker。
面试模型仍按既有 WebSocket 流程执行。Celery 两个进程保持两份 PDF 容量，每份三页视觉并发不变。

Redis 仅绑定 `127.0.0.1`/`::1`，随机密码保存在 root 私有配置；0 号库存消息，1 号库存
短期输入与进度。禁用 RDB/AOF，512 MiB 上限、noeviction，避免候选人资料写入 Redis 持久文件。
Celery 消息只包含随机任务 ID；正文被 worker 原子取走并删除，禁止重复消息重新调用模型。
正常完成或取消清理临时键；进程崩溃时正文/事件最多保留一小时，存活标记在秒级过期。
客户端停止续期十秒后 worker 取消；worker 心跳失效则前端明确报错。已被模型服务接收的请求
仍可能计费。禁用任务重试、发布重试和 broker 自动重连；故障后需排查并显式重启，不切回 inline。

本地默认 `PDF_TASK_EXECUTION=inline`；显式配置 `celery` 时需要启动 Redis 并设置
`CELERY_BROKER_URL` 和 `PDF_TASK_REDIS_URL`，随后在 backend 运行：

```bash
celery -A config.celery:app worker --loglevel=WARNING --concurrency=2
```

生产配置强制 Celery。环境、密钥与模型参数位于 `/etc/ai-interviewer/app.env`；
Redis 专用配置为 `/etc/redis/ai-interviewer.conf`。两个应用服务均由 systemd 管理并开机启动。

## 自动部署

### 简历修复的定向发布（2026-10-09）

发布前核对发现运行中的 `0024583ec6bb922cf1f9ab8799c076aadb37e421` 目录还有数字人连接专项修改，
涉及 ASGI、settings、URLs、数字人诊断页/播放器、面试页面/语音和共享 i18n，尚未进入当前 main 基线。
因此本次简历修复使用独立 `fix/resume-extraction-20261008` 分支，避免触发 main 整包部署覆盖这些文件。
后续整版本发布前必须先将线上数字人实现及新增文件纳入发布基线。

定向发布仅应用该提交的文件差异；共享 i18n 在生产版本上应用简历相关补丁，保留数字人词条。
先备份 PostgreSQL 和目标文件、校验补丁和当前文件摘要，再停止 ASGI/Celery、安装预先验证的文件并重启。
不修改私有环境、依赖、数据库 schema、模型或数字人源文件；保留既有整版本标识，另记录补丁提交及每个部署文件摘要。
回执位于服务器 `/var/backups/ai-interviewer/resume-<提交SHA>/receipt.json`；原文件备份与部署日志放在同目录。
完成条件包括源文件摘要核对、原有数字人代码保持一致、系统服务、公开 HTTPS、既有 smoke 和简历专用验证。
该过程没有自动回滚或自动重放业务请求；失败须保留诊断现场并明确处理。

仓库 `.github/workflows/deploy.yml` 在 **push 到 main** 时自动运行，也可在 Actions 手动触发。
本地修改或仅 commit 尚未 push 不会发布。流程先运行核心、后端、前端测试，全部成功后部署。
Actions 使用独立 `DEPLOY_SSH_KEY` 和固定 `DEPLOY_KNOWN_HOSTS`；密钥只允许执行发布入口，
禁用交互 shell、端口转发和代理转发，不使用 root 密码。服务器入口由 root 安装为
`/usr/local/sbin/ai-interviewer-deploy`，更新该入口须显式审查并安装新版脚本。

发布按完整 SHA 建立独立目录及虚拟环境，进行配置检查、迁移差异检查和 PostgreSQL 备份，
再停止 ASGI/worker、执行迁移、切换 current 并启动服务。发布会短暂重启服务，现有面试连接将断开，请在空闲时段推送。随后检查 HTTP、数据库、Redis，
并实际发送/消费一个无模型调用的 Celery 探针。验证成功后写入 `/opt/ai-interviewer/deployed-revision`。
并发 workflow 串行运行，服务器另有文件锁；同一成功版本再次部署只验证健康。

失败在 Actions 中明确显示，日志保留；没有自动重试或数据库回滚。数据库迁移失败时保持停服，
人工审查备份和 schema 后恢复。失败发布目录保留，修正代码用新提交部署。旧版本和数据库备份
不会自动删除，目前需管理员按磁盘占用维护。

验证记录：main 推送已实际触发 GitHub Actions 完成测试和服务器切换；线上 PDF 经 Redis/Celery、沙箱和真实视觉模型返回 result，取消在 worker 日志确认，损坏 PDF 明确返回 error。发布归档已验证普通文件可用，拒绝路径穿越、链接和环境秘密文件。

## 面试安全引擎临时停用

后端默认 `AI_SECURITY_ENABLED=true`。算法开发期间显式设置 `AI_SECURITY_ENABLED=false`，
并重启后端：面试网关不创建安全引擎或审查模型，跳过安全扫描预算和语义审查。
这是明确的开发配置，不是审查失败后的自动放行；重新设置 `true` 并重启即可恢复原有检查。
独立安全引擎 CLI 与离线评测不受此后端开关影响。

登录、数据归属、请求生命周期、公开输出结构、正文摘要及保存时的状态版本检查仍执行。
开发输出的内部收据为 `status=disabled`，区别于实际审查通过的 `status=allow`；
历史请求接口通过 `security_review_status` 返回实际状态。重新启用不会把旧开发结果改成已审查。
生产配置位于 `/etc/ai-interviewer/app.env`；修改前备份，修改后重启应用服务。
