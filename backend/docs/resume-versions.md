# 简历版本与面试历史接口

本接口补齐简历版本、面试绑定、本人历史与处理状态。使用现有 Django 登录会话；POST/DELETE 需要 CSRF token。新版本资源不提供匿名访问，跨用户资源返回 404。原有文本 WebSocket 和 `/api/resume/parse/` 保持可用。

## 创建与查询版本

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/api/resume-versions/` | JSON `{ "label": "秋招", "text": "..." }` 或 multipart `label` + 单个 `file` |
| GET | `/api/resume-versions/` | 分页查询本人版本元数据，正文与文件不出现在列表 |
| GET | `/api/resume-versions/{id}/` | 查询元数据、text 和 can_interview |
| POST | `/api/resume-versions/{id}/parse/` | uploaded PDF 显式解析；可选 JSON mode=traditional/advanced，默认 traditional，返回 NDJSON |
| POST | `/api/resume-versions/{id}/current/` | 将 ready 版本设为唯一当前版本 |
| GET | `/api/resume-versions/{id}/download/` | 私有下载原 PDF；文本版本没有 PDF |
| DELETE | `/api/resume-versions/{id}/` | 删除未被面试/编辑稿引用且非解析中的版本，成功返回 204 |

每次创建产生新 UUID，内容不提供 PATCH/PUT；当前选择不改写历史版本。客户端必须显式选择版本启动面试，后端不会隐式使用 current。文件上限沿用 10 MiB，PDF 上传只检查输入大小与 PDF 标识，实际结构、页面及视觉校对由原解析管线验证。

PDF 初始状态为 uploaded；显式解析进入 parsing，收到 result 并成功保存文本后为 ready，收到 error 为 failed，已开始的流取消为 interrupted。文本版本直接 ready。HTTP 200 不表示解析成功，前端必须消费终态事件并可查询详情确认状态。ready 表示文本可供面试输入，不表示已完成 Agent 结构化候选人资料提取；后者仍由 prepare/start 完成。

解析沿用 `PDF_TASK_EXECUTION` 和资源限额，默认传统提取；advanced 显式启用视觉校对，不自动回退。视觉响应的 JSON/schema 错误按 `RESUME_VISION_JSON_RETRIES` 有界重试并记录诊断，详见 [PDF 解析](resume-pdf.md)。版本元数据 extraction_mode 记录选定模式；未解析或旧记录留空。失败版本不能再次 parse，可显式重新上传形成新版本。进程崩溃或流尚未开始就断开可能留下 parsing；该状态不能被当成成功或自动重放。

原 PDF 存在数据库 BinaryField，正文与文件通过本人授权接口读取，无公开媒体地址；删除未绑定版本时字节随记录一并删除。数据库备份应包含这些私有文件。已被编辑稿引用、绑定面试或正在解析的版本删除返回 409 `resume_in_use`。

## 绑定面试

通过 `/ws/agent/` 发送已有 prepare/start 命令，使用 `resume_version_id` 替代 `resume_text`，两者恰好提供一个：

```json
{
  "type": "start",
  "request_id": "客户端生成的 UUID",
  "resume_version_id": "本人 ready 版本 UUID",
  "job_title": "Software Engineer",
  "progress_events": true
}
```

其他参数和默认值保持不变。后端在收费调用前校验登录归属和 ready 状态，将版本文本交给现有安全网关、解析器与 Agent。版本不可用返回 `resume_unavailable`，不运行 Agent。prepare/start 的请求预留事务再次核对版本，保存引用和实际输入快照；初始化后的 context 保存实际候选人资料，避免重复维护第二份结构化状态。后续上传或 current 切换不影响面试。旧文本输入保持原存储行为，不自动创建版本。

## 历史与处理状态

- `GET /api/agent-interviews/?status=completed`：本人分页历史，可按现有生命周期 status 过滤，列表新增 resume_version_id。
- `GET /api/agent-interviews/{id}/`：原有问题、回答、已检评价和报告，新增 resume_version_id 与 processing（最近请求的 id/kind/status/error_code/时间）。
- `GET /api/agent-interviews/{id}/requests/`：本人面试请求分页元数据。
- `GET /api/agent-interviews/{id}/requests/{request_id}/`：已检查的请求结果。

`processing.status=running` 仅表示数据库尚无终态，不保证后台仍在运行。接口不读取未经放行的候选人上下文或报告，沿用安全批准凭据验证。can_resume 仍为 false；本次不增加断线恢复、自动重试、评价状态新语义或评分改动。前端继续使用现有 progress/error/report_narrative_status，不能将 evaluation 的 partial 擅自解释为系统故障。

## 验证边界

`interviews.tests.test_resume_versions` 覆盖真实数据库、版本 REST 权限、状态流、删除限制与 WebSocket 绑定。模型、视觉解析和安全审查使用显式替身，因此测试不证明真实供应商或生产 Celery 可用。

## 统一个人中心

登录后访问 `/resumes/`，主页、面试页及诊断页的账号导航均提供“个人中心”入口。
该页面及 `/stream-demo/resumes.html` 别名在本地模式下也要求登录，HTML 和接口响应禁止缓存。

页面支持命名版本、上传 PDF 或粘贴文本、分页历史、在线单元编辑及只读原文对照、下载本人原 PDF、选择当前版本和确认删除。
PDF 通过一个“上传简历”入口选择文件，选中后一次保存原件，无重复确认上传按钮；取消选择不创建版本。
仅待解析附件显示相邻“解析简历”和 traditional/advanced 选项，解析仍明确点击触发，默认 traditional。
历史中的“查看 / 编辑”可选回待解析附件；解析集中在附件区，取消按钮仅在解析期间显示。
粘贴文本仍需显式“保存文本”；版本名称、内容目录、原文对照、可选推荐字段及历史次要操作默认折叠。
用户正文只写入 DOM 的 textContent/value，不保存在浏览器 localStorage；高级模式最终失败后，前端不自动采用中间文本或重新提交解析。
解析流完整成功并查询到 ready 持久化状态后显示完成；取消或断流明确提示刷新，未把 HTTP 200 当作成功。
解析过程中禁用并发写操作，正在解析或已绑定面试的版本遵循后端删除限制。

个人管理区显示只读用户名、注册日期和版本总数，并维护姓名和联系邮箱，退出沿用现有账号导航。
`GET /api/profile/` 返回本人 `id/username/first_name/email/date_joined`；`PATCH /api/profile/`
仅允许提交 first_name（姓名，最多 150 字符）和 email（格式校验，最多 254 字符），均可明确清空。
账号身份、权限、密码等字段拒绝写入；姓名/邮箱只保存到本人用户表，不发送验证邮件或改变登录方式。
写操作需 session 与 CSRF，响应禁止私有缓存，日志只记录用户 ID 和修改字段名。

`/agent/` 和 HTML 别名同样要求登录。面试页仅选择 ready 版本，不再上传、编辑或显式提取 PDF。
初次加载本人全部分页元数据后，优先选择 URL 中 resume_version_id，未指定时选择后端 current。
URL 指定版本不可用、无 current 或无 ready 时要求用户明确选择或前往个人中心维护，不静默选择其他版本。
刷新列表保留原选择；版本不可用时明确要求重选。个人中心的“使用此版本面试”链接传递 UUID，
面试 start 发送 resume_version_id，不发送客户端正文；原有岗位/预算、后端语义解析与评分保持不变。
密码修改与岗位列表 UI 不在个人中心此次改动范围；推荐槽位先提供规则提取建议，用户保存后才作为确认值。

`interviews.tests.test_resume_page` 使用真实 Django 会话/模板验证页面保护及转义。
`interviews.tests.test_profile` 验证本人资料持久化、字段限制和真实 CSRF。
`node --test tests/resumes-client.test.mjs` 使用真实客户端和显式 REST/DOM 替身验证上传、两种解析模式、失败/断流及版本操作；这些替身测试不能替代真实浏览器或供应商验证。
未选择版本时隐藏空编辑表单；初始打开当前页 ready 的 current，其余情况由用户明确选择。

## 在线编辑稿与推荐资料

上传原件后，附件旁的“解析简历”启用；传统提取仍为默认，高级多模态为显式选项。
左侧分区导航、可展开的单元卡片和底部保存栏支持逐项核对；只读原文对照展开时才使用双栏。
原始 PDF 字节与原解析文本保留，编辑后的单元文本存为新 UUID 版本，不覆盖原件或旧稿。
每次保存记录 source_version（原件根）与 edited_from（本次编辑基线），不自动设为 current。
未保存修改离开/切换前提示；保存失败保留输入，无自动保存、重试或隐式推断。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/resume-versions/{id}/editor/` | ready 版本的 units、原件只读文本、slots、字段所属单元及 review_warnings |
| POST | `/api/resume-versions/{id}/editions/` | `{label, units, slots}` 创建独立 ready 编辑快照 |
| GET | `/api/resume-versions/{id}/export/` | 下载此版本的 UTF-8 文本，PDF 原件仍用 download |
| GET | `/api/resume-versions/{id}/recommendation-profile/` | 返回可用于现有推荐接口的 candidate 与 slot_units |

固定单元为 basic（简介/联系）、education（教育）、experience（实习/工作）、projects（项目）、
skills（技能）、awards（荣誉/证书）、publications（论文/研究）、preferences（意向）、other（其他）。
原件初次打开仅按独立中英文标题分组，标题由单元标签表示；匹配时统一大小写、空白、连字符和 `&`/`and`。
支持 `INTERNSHIP EXPERIENCE`、`PROJECT EXPERIENCE`、`CORE SKILLS`、`OPEN-SOURCE CONTRIBUTIONS & PUBLICATION`、`HONORS & AWARDS` 等常见标题。
非标题行字符保留并归入当前单元；首个标题前有明确邮箱或联系电话标签时归入 basic，否则归入 other。
这不是语义抽取，用户应核对并移动文本。编辑稿沿用已保存的单元，不重新分组覆盖用户修改。
保存时补齐缺失单元为空字符串、按固定顺序组合非空单元；
正文含标题总计不超过 200000 字符，单元内用户文本不改写，空正文拒绝。

`review_warnings` 对同一单元内至少 16 个连续词、80 个字符的重复片段提供最多五条核对提示，
包含 `code=repeated_passage`、`unit`、从 1 开始的 `line` 和原文片段 `text`。仅忽略匹配时的空白差异，
不删除内容、不断言重复必然错误，也不改变保存状态。提示对应载入快照，手工编辑时不会实时重算。
正文输入框按内容估算初始高度并限制在 4–20 行，CSS 进一步限制视窗高度；仍可滚动、调整高度及编辑完整文本。
技能、专业和兴趣关键词使用全宽多行输入，支持逗号、分号或换行分隔，便于核对较长列表。

推荐槽位与原实验 CandidateInput 一致：技能/兴趣/专业关键词，GPA、在读学业阶段、累计经验月数、
论文数量、工作方式、每周小时、可投入月数及暑期意愿。未填为 null，不填补 0/false；已保存的明确 [] 保留。
提取和新编辑稿持久化均固定包含以下全部业务字段，缺失键在保存验证时由 CandidateInput 的默认值补为 null；
读取旧版稀疏记录时也返回完整字段，不回写历史记录。手工提交类型错误仍返回验证失败，不把错误输入伪装成缺失。

| 字段 | 含义 / 已知值类型 |
|---|---|
| skills | 技能关键词列表 |
| interests | 兴趣 / 目标方向关键词列表 |
| majors | 专业关键词列表 |
| in_person_commitment | 工作方式，沿用模型枚举 |
| gpa | 原成绩制 GPA 数值 |
| months_experience | 累计经验月数 |
| academic_level | 当前在读阶段，UG1–UG4 / MS1–MS2 / PhD1–PhD5 |
| hours_per_week | 每周可投入小时数 |
| length_of_commitment | 可连续投入月数 |
| num_publications | 已发表论文数量，非负整数 |
| commit_to_summer | 是否可参与暑期工作，布尔值 |

以上各项均允许 null，`candidate_id` 由版本 UUID 提供，不从简历猜测或允许用户覆盖。
英文自然标签也覆盖全部字段，例如 `Experience (months)`、`Current study stage`、`Hours per week`、
`Commitment (months)`、`Publication count`、`Working arrangement` 和 `Available in summer`；
明确英文年级、工作方式和 Yes/No 映射到原有枚举/布尔值，不改写关键词大小写或推算经历日期。
未知项可留空保存；部分字段有值时可走原推荐流程，全部字段均未知时仍提示资料不足。
模型特征构造继续把未知项表示为 NaN，不伪造成零分特征；评分模型、预处理、单位及阈值均不变。

原件 `editor` 响应提供 `slot_suggestions`，包含全部 11 字段的 `values`、原文 `evidence`、
问题码 `issues` 和未知字段名 `missing`。依据明确标签及技能单元列表规则提取，无收费模型请求；
目标岗位标签（如 `Target Roles`）可形成兴趣关键词建议；教育单元中的明确 `degree in discipline`
写法可形成专业建议并保留原行证据，仅有 `Master of Science` 等学位名时不猜专业。
数值沿用原 GPA/月/小时单位，不按日期推算年级或经验，不从学校/项目猜测偏好。
不支持的格式、矛盾标量或契约超限值保持未知并保留证据，供用户补充；不静默选值或改换提取实现。
前端填入有依据的建议并展开对应字段，提示核对保存，未保存时不能设为当前且保护离开。
原件 GET 不写库也不直接确认；保存为编辑稿后 recommendation-profile 才读取确认槽位。
编辑稿 `slot_suggestions` 为 null，不重新提取覆盖用户修改、清空和历史快照；原件与原文仍保留。
前端关键词分隔符只分隔条目与去除条目外空白，保留大小写；应与岗位数据关键词一致，不改变模型预处理。
GPA 沿用原成绩制，学业阶段不是岗位资历，不能从项目文本猜测经验数值。
姓名/邮箱与项目全文不作为当前模型评分特征；九个单元全文持久化，可供面试和后续经明确设计的文本推荐使用。

示例：保存 `{ "units": { "projects": "API 项目", "skills": "Python" }, "slots": { "skills": ["Python"], "months_experience": 0 } }`。
保存返回新 id；获取该 id 的 recommendation-profile 后将 candidate 与岗位池组合，调用现有
`POST /api/recommendations/jobs/`。此接口不自动加载岗位池、不执行排序或调用模型。
仅本人可访问这四个动作，非 ready 返回 400，来源/基线仍被编辑稿引用时删除返回 409。
测试见 `interviews.tests.test_resume_editor`；数据库/API 真实执行，推荐复用验证为严格 schema，非模型效果评估。

2026-10-08 使用用户授权的单页 PDF 和其粘贴的提取结果在隔离本地数据库验证：两种文本均分成七个有内容的单元，
识别技能、专业、目标岗位、GPA 四类待确认建议；粘贴样本的两处长重复显示核对提示，正文保留。
同一 PDF 的真实视觉调用首次缺少两个 `corrections.*.end_line`，第二次通过 schema；该个例不证明首次成功率或全部内容正确。
原文件和实际输出仅保留在被 Git 忽略的本地调试目录，回归用例使用合成数据。后端 editor/slots/page/versions 共 27 项、
前端客户端 23 项回归通过，并进行了真实浏览器展示检查；未验证生产部署。

## 面试进度与复盘（2026-10-03）

工作台在首题就绪后展示剩余时间、Agent 当前阶段、当前题号、预计总题数与安全上限。
剩余时间以获准 question 响应的 `interview_state.remaining_seconds` 为锚点，用浏览器单调时钟推进；
模型等待期间继续计时，结束或断线冻结。不会将服务端 `clock_started_at` 与浏览器时间相减，
也不会因客户端显示 0 自动提交回答。原有后端预算、题数与收尾策略不变。

question 响应新增 `plan_history`、`topic_progress`、`decision_logs`，与问题、状态、计划和评价
一起经过完整输出检查，并在发送前保存。当前计划 topics 的 `expected_questions` 已由 Agent
包含累计已问数量，预计总题数按当前题号加未完成话题的剩余配额计算，再限制到 `max_questions`；
完成或跳过的话题不再增加预计题数。估计随计划修订变化，不是必须完成的题量。

历史详情仍使用 `GET /api/agent-interviews/{id}/`，新增 `schema_version: 2`、`interview_plan`、
`plan_history`、`topic_progress`、`decision_logs` 和 `request_issues`（失败/中断请求的固定元数据）。
`questions[].created_at` 与 `questions[].answer.created_at` 为保存时间；原 `questions`、评价、
最终报告、`processing`、`can_resume` 契约保留。新增模型正文仅读获准响应，不公开内部 context；
旧获准响应缺字段返回 null，未获准或摘要被篡改的内容不会自动公开。

顶部导航“面试复盘”进入独立 `/interview-review/` 页面，提供分页列表和滚动详情弹窗，展示难度、追问深度、问答、获准评价、
报告总结、优势与待改进项，以及话题进度和可展开诊断。失败/中断过程与未评价答案明确标注，
没有完整报告时不伪造评分。工作台与复盘显示已有检索失败/超时、备用路径和报告叙述失败标志；
连接/协议异常仍停止当前流程，不增加重试、恢复连接或模型备用策略。

### 独立复盘导航

面试复盘与个人中心在顶部导航并列。个人中心仅维护资料、简历和岗位推荐，不加载复盘模块或
查询面试列表。`/interview-review/` 及其 `/stream-demo/interview-review.html` 别名均要求登录，
包括关闭全局登录门禁的本地模式；详情继续调用原本人历史 API，数据与访问权限不变。
面试异常和报告中的复盘链接直接指向此页面。
