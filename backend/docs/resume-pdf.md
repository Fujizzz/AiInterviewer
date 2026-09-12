# PDF 简历提取与视觉整理

在 `/agent/` 选择 PDF 后点击“解析并校对 PDF”，一次上传依次完成：
传统库提取 → 多模态 LLM 对照原页校对 → 直接展示校对后的文本及必要疑点 → 人工采用。
模型以提取结果为基线，只提出有明确视觉依据的必要修改；无需调整时程序逐字保留基线。
失败或取消时不展示或采用中间文本；不会降级成仅规则成功。

## 模块职责

| 层次 | 实现 | 职责 |
| --- | --- | --- |
| 后端规则 | `interviews/resume_pdf.py` | pypdf 布局提取，保留原文，修复明确排版连字、换行和行尾空白；PDFium 渲染 |
| Agent | 根目录 `agents/resume_cleanup.py` | 独立 `VisionPort.review`、单页校对补丁契约，验证来源定位、非重叠性及非空页遗漏 |
| 模型适配 | `interviews/resume_vision.py` | 异步 Chat Completions 图文调用和 JSON 校验，复用所选供应商凭据 |
| HTTP | `interviews/resume_api.py` | 有界上传读取、线程中规则/渲染、异步视觉调用及阶段流 |
| 界面 | `frontend/resume-pdf.js` | 等待计时、最终文本/疑点展示、取消和人工采用 |

视觉 Agent 只校对字符与页面内阅读顺序，不抽取候选人能力、不评分、不润色简历，
不补充跨页句子或缺失经历。后续既有 `app/parsing/resume.py` 仍负责将用户确认的文本
提取为 CandidateProfile。面试策略、评价公式、模型和预算默认值保持原配置。
CLI 的既有 `read_resume` 文本 PDF 路径未替换；此新增流程由后端页面/API 提供。

## 配置

安装 `backend/requirements.txt`（新增 PDFium/Pillow；根目录 CLI 环境无需这两项）。
整个流程需要视觉模型，在 `backend/.env` 显式设置独立供应商和模型，例如：

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
按实际完成数量更新进度，最终结果按原页序合并。该上限是每份上传的窗口，不是全服务限流；
单页 PDF 不产生页间并发。超时为每页请求的 SDK 超时，非整份文档截止时间。

## 上传与事件协议

`POST /api/resume/parse/`，同源 multipart：仅提交 `file` 为单个 PDF，不接受 `mode` 或其他字段；原两种模式接口已移除。
HTTP 参数错误返回 4xx JSON；进入处理后为 `application/x-ndjson`，每行一个 JSON 对象：

1. `progress`：`stage`、`detail`，表示规则提取、渲染及视觉校对已完成页数。
2. 校对逐页返回 `page`：`number,text,changed,uncertainties`，到达顺序可能与原页序不同。
3. `result`：成功终态，包含 `text,pages,changed_pages`；pages 按原页序排列。
4. `error`：失败终态，含 `stage,detail,request_id`，不再返回 `result`。

不再发送规则中间结果或 `corrections`；修改建议只用于后端校验。前端只在完整成功终态后
显示最终文本和按页排序的必要疑点，不打印修改前、修改后或修改理由。

HTTP 200 不代表模型成功；客户端必须等到 `result`。断流和取消不得标记完成。
视觉失败后界面不展示未完成文本，也不允许采用部分结果。
修改预览或选择文件不会隐式调用模型；每次按钮点击都会重新上传并独立解析，没有跨请求缓存。

内部模型返回 `PageReview{number,corrections,uncertainties}`，每条修改为 `{before,after,reason}`。
`before` 必须在规则基线中唯一出现，且修改片段互不重叠；Agent 在原基线上定位，按位置
从后向前替换。未修改的文字、空白和顺序由代码保留，不依赖模型重新抄写。
空提取页允许一条 `before=""` 补录可见文字；空白页无需修改。错误定位、重叠、无效补丁、
错误页码及整页删除均失败，不重试或替换成另一套转写结果。结构校验不能证明视觉依据真实。

## 数据与资源边界

- 仅接受 1–10 页、非空且最多 10 MiB 的 PDF；加密、超限或损坏文件不截断、不尝试解密。
- 单页规则文本最多 30000 字符；PNG 最长边最多 1800 像素，最高缩放 2.5 倍。
  小字、复杂图表和低清扫描可能仍然不可读，需要用户核对原 PDF。
- 规则层保留行列空格，不推测分栏顺序、不删除重复条目、不自动连接断词。无文本页保留并提示扫描/空白可能性。
- PDFium 非线程安全，整个原生对象生命周期使用同一互斥锁。CPU 提取和渲染在工作线程执行，
  取消不能抢占已经执行的线程；线程结束时释放资源。尚未实现进程隔离或对恶意压缩流的硬内存/CPU 限额。
- PDF 页面图片和文字在用户点击“解析并校对 PDF”后发给配置的供应商。业务不落库、不保存 PDF/PNG；
  Django 可能将上传数据暂存系统临时目录，响应关闭时由框架清理。不使用供应商文件上传或远端图片 URL。
- 日志只记录阶段、请求 ID、字节数、页码、模型、时长及异常类型，不记录文件名、文本、图片或密钥。
- 单页失败、取消或流关闭时停止补充并发窗口，取消并等待所有在途页之后再关闭共享客户端。
  无法保证供应商已接收请求停止执行或计费。
- JSON/页码校验不等于 OCR 准确性校验；提示要求不猜测，并输出 `uncertainties`。不确定时保留原文。
  模型未报告疑点也仍需人工核对，尤其是姓名、联系方式、日期和数值。

## 实现依据与验证入口

- [pypdfium2 生命周期与线程限制](https://pypdfium2.readthedocs.io/en/stable/python_api.html)
- [DashScope 视觉输入](https://help.aliyun.com/zh/model-studio/vision)
- [DashScope JSON 输出支持](https://help.aliyun.com/zh/model-studio/qwen-structured-output)

本地测试命令：`python manage.py test interviews.tests.test_resume_pdf`、
`node --test tests/resume-pdf-client.test.mjs`。使用合成 PDF 和模拟视觉 SDK；
测试范围及真实模型联调记录见 [测试说明](testing.md)。
