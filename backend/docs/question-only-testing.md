# 仅出题的真实面试测试

`backend/tests/run_live_twenty_minute.py` 和 `backend/tests/run_live_intro_rag.py` 默认只测试出题流程，不启动后台能力评分、正式多阶段评分，不等待评分收尾，也不生成数值评分报告或文字报告。

实时回答分析保留：仍调用 `CompactAnswerDecision` 判断回答范围、已覆盖内容、未回答细节、矛盾和拒答，供追问、换话题和重规划使用。计划、缓存检查、实时出题、RAG 和题目质检继续使用原有逻辑。数据库中仍保存回答及用于推进会话的分析反馈；这不表示正式评分已执行。

该设置通过 `backend/tests/live_question_session.py` 的测试专用组合生效，不修改 `.env`、网页后端的 `AgentSession`、生产 evaluation 配置或评分实现。仅将 `EVALUATION_MODE` 改成 `legacy` 仍会运行后台能力评估，不能替代此测试组合。

继续复用数据库里的简历结构和预生成题目，例如在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe backend/tests/run_live_twenty_minute.py `
  --duration-minutes 15 --min-answer-seconds 35 `
  --reuse-database output/live_15min_20261016T050003Z/benchmark.sqlite3 `
  --resume-version-id 147320ef-f6cb-4d7c-adbd-21efad50e01d
```

该脚本仍通过输出目录的 `current_question.json` 和 `answers/answer_NNN.json` 等待 Codex 撰写候选人回答；不是自动填充回答的独立压测程序。复用时只读源数据库并创建私有副本，不重新解析简历或预生成。

确需验证评分时，显式添加 `--with-evaluation`，才恢复原来的评分和报告流程。这个开关会增加真实 API 消耗。

测试记录标记 `evaluation_tested=false`、`evaluation_mode=questions_only`、`report_generated=false`，统计中的最终评分状态为 `not_tested`，不会把未测试的评分显示成评分失败或零分。长测审计还会在 SDK 请求之前阻止意外进入评分或报告模型。

验证：新增测试通过真实后端命令和数据库事务完成自我介绍、技术回答及结束面试；实时分析保持工作，评分模型及报告生成被断言禁止，三类后台评分表均无新增记录。新测试与自我介绍、Agent 回归共 22 项通过，CLI 帮助、脚本编译和相关 Ruff 检查通过。本次没有调用真实 API，也没有启动新的长测。
