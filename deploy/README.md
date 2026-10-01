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
| 数据备份 | `backup-postgresql.sh` 手动创建 `/var/backups/ai-interviewer/*.dump` |

服务器使用新建数据库，只由既有迁移初始化两道练习题；未导入本地 SQLite 历史。
部署使用 Git 提交的源码归档；未上传 `.git`、本地数据库、日志、缓存或虚拟环境。
Redis/Celery 接入不修改模型、采样参数、评分策略、推荐权重、PDF 参数或容量限制。
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
