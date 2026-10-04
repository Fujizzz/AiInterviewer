# Evaluation 与 main 整合记录

整合日期：2026-10-04。独立分支：`codex/evaluation-main-integration`。

- 本地评分接入提交：`369b7ec9e515f9f164a21d55e6bbf9c763b08b91`。
- GitHub main 基线：`14e190223b45bf4643bc16ad709cc04b2ddd2fee`。
- 共同祖先：`a13588d9b0df443a178351fe473602a38a807464`。

## 冲突清单与处理

Git 三方合并没有文本冲突。以下问题属于自动合并无法发现的运行或工程约束冲突：

| 冲突 | 涉及代码 | 影响与处理 |
| --- | --- | --- |
| 数据库迁移双叶节点 | `backend/interviews/migrations/0010_evaluation_ledger.py` 与 `0010_agent_automatic_end.py` | 两个迁移都依赖 `0009_resume_editions`，直接迁移会被 Django 拒绝。新增 `0011_merge_evaluation_automatic_end`，保留双方原编号及操作，兼容已经应用任一分支的数据库。 |
| 评分日志保护来源与放弃面试冲突 | `backend/interviews/evaluation_models.py`、`agent_records.py` | 评分日志对回答/请求的 PROTECT 外键阻止原 discard 删除回答。归属检查通过后，在原事务内先删除本面试评分日志；错误全部回滚。正常评分仓库仍只追加，明确放弃整个面试是保留规则的例外。 |
| 静默跳题被当作评分遗漏 | `evaluation/integration.py`、`persistence.py`、`agents/domain/models.py`、`app/adapters/rubric_evaluation.py` | skip 按设计不调用模型、不产生评分日志，但后续评分会把它算作 unassessed_feedback。原子保存无回答标记 `unobserved_feedback_ids`，在重启及历史裁剪后仍能区分；实际评分失败与普通缺失记录继续阻断发布。 |
| 新后端代码不满足 main 的注释检查 | `agent_repository.py`、`evaluation_models.py`、`0010_evaluation_ledger.py`、`test_evaluation_persistence.py` | 为新增声明补齐英文实现说明、Declaration Index 和 Variable Index，更新评分提交/删除边界说明。 |

另外同步了 `uv.lock` 中 main 已声明的 `completion-gate` 可选依赖。没有升级原锁文件已有包的版本。

双方同时修改了 5 个文件，已核对并保留两边功能：

| 文件 | 合并结果 |
| --- | --- |
| `.env.example` | 同时保留语义结束配置与 `EVALUATION_MODE`。 |
| `agents/orchestrator/service.py` | 保留主分支的 finish/自动结束流程，评分 receipt 随同最终或下一轮状态原子提交，公共历史只存安全反馈。 |
| `backend/interviews/agent_repository.py` | 同时支持 answer/skip/finish 请求和评分日志 CAS/回放校验。 |
| `backend/interviews/models.py` | 同时注册已有模型与 AgentEvaluation。 |
| `backend/interviews/tests/agent_fixtures.py` | 保留英文/结束流程 fixtures，增加新评分 schema 的离线输出。 |

提前结束带当前回答时，保存当前评分与最终状态；不带回答时，只保留已有日志且不生成下一题。
main 的前端语言切换、语义结束模型/MCP、自动计时与静默结束功能均保留。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 根目录 pytest | 730 passed，5 subtests passed |
| Django `manage.py test interviews --noinput` | 208 tests，OK |
| Node 客户端/数字人测试 | 80 passed |
| Ruff | All checks passed |
| 后端 `tools/check_docs.py` | problems=0 |
| `makemigrations --check --dry-run` | No changes detected |
| 空数据库迁移 | 通过，两个 0010 与合并节点均应用，skip/finish 约束有效 |
| 已应用 `0010_agent_automatic_end` 后升级 | 通过 |
| 已应用 `0010_evaluation_ledger` 后升级 | 通过 |
| Git diff whitespace check | 通过 |

新增数据库回归覆盖：提前结束有/无当前回答、静默跳题后裁剪历史并重启评分仓库、
错误归属不能删除、删除中途失败回滚、成功放弃时清理评分与来源记录。
原评分测试继续覆盖 CAS 冲突、幂等、后置事务失败、来源篡改、失败快照恢复和私有数据隔离。

本机验证使用独立测试数据库及离线模型。macOS LightGBM 使用已有的临时 libomp；
HTTP 测试需要回环端口权限，并对 localhost/127.0.0.1 设置 NO_PROXY，避免系统代理
将本地探针错误转发到线上地址。这些环境配置不写入产品设置。

## 发布边界

先推送独立整合分支，再快进更新 main，不强推、不改写同事提交历史。
主分支现有 GitHub Actions 会执行测试并自动部署。部署前需要应用新增迁移。
默认仍采用 shadow 评分：用户可见反馈和分数沿用旧流程，新评分保存为内部审计记录；
可用 `EVALUATION_MODE=legacy` 关闭新增模型调用。此次离线验证不证明真实模型评分质量或
真实麦克风效果，也不等同于线上部署成功。
