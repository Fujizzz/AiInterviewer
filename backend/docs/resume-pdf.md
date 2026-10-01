# PDF 简历提取与视觉整理

在个人中心 `/resumes/` 上传保存 PDF 版本，选择提取方式后点击“解析 PDF”：

- **传统提取（默认）**：传统工具提取 → 保存文本 → 人工核对预览；不调用模型或向供应商发送页面。
- **高级提取（多模态 LLM）**：传统库提取 → LLM 对照原页校对 → 保存文本并展示疑点 → 人工核对。
模型以提取结果为基线，只提出有明确视觉依据的必要修改；无需调整时程序逐字保留基线。
失败或取消时不采用中间文本；高级模式不会降级成传统成功。模式对下一次显式解析生效，不修改已有版本结果。

## 模块职责

| 层次 | 实现 | 职责 |
| --- | --- | --- |
| 后端规则 | `interviews/resume_pdf.py` | pypdf 布局提取，保留原文，修复明确排版连字、换行和行尾空白；PDFium 渲染 |
| Agent | 根目录 `agents/resume_cleanup.py` | 独立 `VisionPort.review`、单页校对补丁契约，验证来源定位、非重叠性及非空页遗漏 |
| 模型适配 | `interviews/resume_vision.py` | 异步 Chat Completions 图文调用和 JSON 校验，复用所选供应商凭据 |
| 隔离运行 | `interviews/pdf_sandbox.py`、`pdf_supervisor.py`、`pdf_worker.py` | Linux/WSL 沙箱内规则提取和渲染，限制资源并监督取消 |
| HTTP | `interviews/resume_api.py` | 有界上传读取、调用沙箱、异步视觉调用及阶段流 |
| 界面 | `frontend/resumes.js` | 统一个人中心内的上传保存、显式解析、最终文本/疑点预览和取消 |

视觉 Agent 只校对字符与页面内阅读顺序，不抽取候选人能力、不评分、不润色简历，
不补充跨页句子或缺失经历。后续既有 `app/parsing/resume.py` 仍负责将用户确认的文本
提取为 CandidateProfile。面试策略、评价公式、模型和预算默认值保持原配置。
CLI 的既有 `read_resume` 文本 PDF 路径未替换；此新增流程由后端页面/API 提供。

## 配置

安装 `backend/requirements.txt`（新增 PDFium/Pillow；根目录 CLI 环境无需这两项）。
还须按 [隔离运行说明](../sandbox/README.md) 配置 PDF 沙箱；缺少环境会明确失败。
仅高级提取需要视觉模型，在仓库根目录 `.env` 显式设置独立供应商和模型，例如：

```dotenv
RESUME_VISION_PROVIDER=dashscope
RESUME_VISION_MODEL=qwen3-vl-flash
RESUME_VISION_TIMEOUT_SECONDS=90
RESUME_VISION_ENABLE_THINKING=false
```

上述配置复用 `DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL`，不复制密钥。
也支持 `RESUME_VISION_PROVIDER=openai`，复用 `OPENAI_API_KEY/OPENAI_BASE_URL`；
此时需要自行选择支持图片和 JSON 模式的模型，并清空 DashScope 专用 thinking 配置。
修改 `.env` 后重启服务。缺配置、模型不支持、超时、拒绝、截断和 JSON 校验失败均明确报错。
`max_retries=0`，每页一次请求，每份 PDF 最多 3 页同时调用（`VISION_CONCURRENCY=3`）。
按实际完成数量更新进度，最终结果按原页序合并。该上限是每份上传的窗口；ASGI 另限制同机最多 2 份 PDF 同时处理。
单页 PDF 不产生页间并发。超时为每页请求的 SDK 超时，非整份文档截止时间。

## 上传与事件协议

`POST /api/resume/parse/`，同源 multipart：`file` 为单个 PDF，可选 `mode=traditional|advanced`，缺省 traditional；其他字段或重复 mode 拒绝。模式随 Celery 任务透传，worker 不隐式改变选择。
HTTP 参数错误返回 4xx JSON；进入处理后为 `application/x-ndjson`，每行一个 JSON 对象：

1. `progress`：`stage`、`detail`，表示规则提取、渲染及视觉校对已完成页数。
2. 校对逐页返回 `page`：`number,text,changed,uncertainties`，到达顺序可能与原页序不同。
3. `result`：成功终态，包含 `text,pages,changed_pages`；pages 按原页序排列。
4. `error`：失败终态，含 `stage,detail,request_id`，不再返回 `result`。

不再发送规则中间结果或 `corrections`；修改建议只用于后端校验。前端只在完整成功终态后
显示最终文本和按页排序的必要疑点，不打印修改前、修改后或修改理由。

HTTP 200 不代表模型成功；客户端必须等到 `result`。断流和取消不得标记完成。
视觉失败后界面不展示未完成文本，也不允许采用部分结果。
个人中心预览只读，选择文件不会隐式调用模型；上传保存和解析为两个显式操作。
每个 uploaded 版本只允许领取一次解析；失败或中断须显式重新上传形成新版本。

内部模型返回 `PageReview{number,corrections,uncertainties}`，每条修改为
`{start_line,end_line,text,reason}`。输入 `rule_lines` 包含后端固定编号的原文行；行号从 1 开始，
闭区间须满足 `1 <= start_line <= end_line <= 行数`。Agent 直接用原文行计算字符范围，
真实修改不得重叠，按位置从后向前替换；未选中的字符逐字保留，不让模型重复抄写修改前文本。
非空替换保留该范围末端的原有 CRLF/LF/CR，空 text 表示明确删除范围；非空页整页删除仍拒绝。
范围有效、理由非空且替换后与该范围原文相同的建议视为无需修改，不参与区间替换或对外 corrections。
空提取页有一个虚拟第 1 行，允许在该范围补录可见文字；空白页无需修改。
越界/逆序行号、真实重叠、缺少依据、错误页码及整页删除均失败；不重试或采用其他转写结果。
提示仅要求返回真实差异和简洁依据；模型、thinking、timeout、温度、渲染和页数参数不变。
该内部协议变更经用户确认，用来减少重复输出并消除模型抄错 before 造成的定位失败。
结构校验仍不能证明图像依据或所选范围的语义正确，最终文本和疑点应由用户核对。

## 数据与资源边界

- 仅接受 1–10 页、非空且最多 10 MiB 的 PDF；加密、超限或损坏文件不截断、不尝试解密。
- 单页规则文本最多 30000 字符；PNG 最长边最多 1800 像素，最高缩放 2.5 倍。
  小字、复杂图表和低清扫描可能仍然不可读，需要用户核对原 PDF。
- 规则层保留行列空格，不推测分栏顺序、不删除重复条目、不自动连接断词。无文本页保留并提示扫描/空白可能性。
- PDFium 生命周期的既有互斥锁保留；生产提取与渲染在独立 Linux 沙箱中执行。
  新增 768 MiB 地址空间、20 CPU 秒和 30 秒墙钟限制；超时或取消由监督器终止工作进程。
  配置、上传前容量控制及局限见 [隔离运行说明](../sandbox/README.md)。
- 仅高级提取将 PDF 页面图片和文字发送给配置的供应商。独立 `/api/resume/parse/` 接口不落库、不保存 PDF/PNG；
  Django 可能将上传数据暂存系统临时目录，响应关闭时由框架清理。不使用供应商文件上传或远端图片 URL。
  个人中心通过私有版本接口保存原 PDF 与最终文本，权限与生命周期见 [版本说明](resume-versions.md)。
- 日志只记录阶段、请求 ID、字节数、页码、模型、时长、异常类型、有限失败码、HTTP 状态、schema 路径与 token 数，不记录文件名、文本、图片或密钥。
- 单页失败、取消或流关闭时停止补充并发窗口，取消并等待所有在途页之后再关闭共享客户端。
  无法保证供应商已接收请求停止执行或计费。
- JSON/页码校验不等于 OCR 准确性校验；提示要求不猜测，并输出 `uncertainties`。不确定时保留原文。
  模型未报告疑点也仍需人工核对，尤其是姓名、联系方式、日期和数值。

## 实现依据与验证入口

错误事件现在附带稳定 `code`：`timeout` 表示视觉请求超时，`invalid_json` 表示响应结构无效，
`resume_correction_range_invalid` 表示修改行号越界或逆序；其他已知补丁错误也有固定码。
未知异常继续返回通用提示，供应商原始异常不公开。所有错误仍不产生成功终态或允许采用部分文本。

可显式运行 `python tools/retest_resume_pdf.py <PDF路径> <私有输出目录>`，使用当前行号协议和已配置模型参数重测，
不重试、不放宽校验；成功时保存页面 PNG 和 result.json。`--http-url` 仅适用于 `/agent/` 可以匿名访问的诊断环境；当前本地页面也要求登录，
该 CLI 不创建登录会话，因此会明确报告 HTTP 设置失败，不绕过认证。默认 traditional，显式 `--mode advanced` 才调用模型。输出含个人简历信息，应保存在仓库外私有目录。

- [pypdfium2 生命周期与线程限制](https://pypdfium2.readthedocs.io/en/stable/python_api.html)
- [DashScope 视觉输入](https://help.aliyun.com/zh/model-studio/vision)
- [DashScope JSON 输出支持](https://help.aliyun.com/zh/model-studio/qwen-structured-output)

本地测试命令：`python manage.py test interviews.tests.test_resume_pdf`、
`node --test tests/resumes-client.test.mjs`。使用合成 PDF 和模拟视觉 SDK；
测试范围及真实模型联调记录见 [测试说明](testing.md)。
