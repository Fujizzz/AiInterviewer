"""将 MVP 面试用例拆为可等待用户输入的网络会话，决策仍由 Agent 核心负责。

目录：
- AgentSession：
  保存一次连接的应用组合和已完成回答；不使用 Django 业务数据库。
- AgentSession.__init__：
  构造一次连接独占的 MVP 应用组合，不启动面试或调用模型。
- AgentSession.start：
  从已验证的 Start 命令初始化候选人、岗位及 Agent，再返回首个可展示动作。
- AgentSession.answer：
  将当前答案转成标准反馈，驱动一次 Agent 决策并返回下一题或最终报告。
- AgentSession._response：
  将 Agent 动作规范化为网络可序列化的 question 或 finished 字典。
- AgentSession.close：
  向支持 close 的模型实例移交清理请求，不主动清空仍被后台调用引用的状态。

关键变量：
（无模块级变量。）

关键状态说明：
AgentSession.interview_id 为会话标识；app 持有本会话仓库与适配器。
history 为展示历史；action 为最近提交的 Agent 动作。
profile、candidate_name、job、service 在 start 中建立；初始化失败时协议层关闭整场会话。
"""

from uuid import uuid4

from agents.config import load_agent_settings
from agents.orchestrator import InterviewAgentService
from app.application import DEFAULT_COMPETENCY_IMPORTANCE, MVPInterviewApplication
from app.parsing.resume import parse_resume_profile
from app.reporting.final_report import build_final_report
from shared.contracts import (
    CandidateAnswer,
    EvaluationRequest,
    InitializeInterviewRequest,
    InterviewActionType,
    InterviewStage,
    JobProfile,
)

from .agent_provider import BackendLLM


class AgentSession:
    """保存一次连接的应用组合和已完成回答；不使用 Django 业务数据库。"""

    def __init__(self, llm=None):
        """构造一次连接独占的 MVP 应用组合，不启动面试或调用模型。

        输入：可选 StructuredLLM；测试显式注入替身，省略时读取真实供应商配置。
        状态：生成 interview_id，创建内存仓库，将 history 置空、action 置为 None。
        异常：模型配置或 SDK 初始化失败直接传播，由协议层返回配置错误。
        """
        self.interview_id = str(uuid4())
        self.llm = llm if llm is not None else BackendLLM(interview_id=self.interview_id)
        self.app = MVPInterviewApplication(self.llm)
        self.history = []
        self.action = None

    async def start(self, command):
        """从已验证的 Start 命令初始化候选人、岗位及 Agent，再返回首个可展示动作。

        前置条件：协议层保证本连接尚未开始；参数类型和范围已通过 Start 校验。
        逻辑：模型解析简历→构造岗位→装配端口→初始化计划→规范化阶段转换。
        预算：沿用 MVP 的题数乘以每题 120 秒，追问上限来自命令，能力权重不变。
        返回：question 响应字典，或 Agent 直接结束时的 finished 响应字典。
        副作用：调用模型并写入本会话内存；异常向协议层传播，失败会话不恢复。
        """
        self.profile, self.candidate_name = await parse_resume_profile(
            command.resume_text, llm=self.llm, candidate_id=f"candidate-{self.interview_id}"
        )
        self.job = JobProfile(
            job_id=f"job-{self.interview_id}",
            title=command.job_title,
            competency_importance=DEFAULT_COMPETENCY_IMPORTANCE,
        )
        # 仅应用用户命令中的追问上限，其他策略取自现有 MVP 配置，避免产生第二套决策参数。
        settings = load_agent_settings().model_copy(
            update={"max_consecutive_probes": command.max_follow_up_per_topic}
        )
        self.service = InterviewAgentService(
            repository=self.app.repository,
            evaluation=self.app.evaluation,
            llm=self.app.agent_llm,
            settings=settings,
        )
        initialized = await self.service.initialize_interview(
            InitializeInterviewRequest(
                interview_id=self.interview_id,
                candidate_profile=self.profile,
                job_profile=self.job,
                duration_seconds=command.max_questions * self.app.seconds_per_question,
                enabled_stages=[InterviewStage.PROJECT_DEEP_DIVE],
            )
        )
        self.action = initialized.first_action
        return await self._response()

    async def answer(self, command):
        """将当前答案转成标准反馈，驱动一次 Agent 决策并返回下一题或最终报告。

        输入：已验证的 Answer 命令；协议层保证会话就绪、问题 ID 当前有效且请求唯一。
        逻辑：构造答案与评价请求→提取证据→追加展示历史→提交反馈→生成响应。
        原子边界：Agent 仓库负责其单轮提交；history 的追加与该提交不是同一事务。
        若追加历史后的 Agent 提交失败，协议层关闭并丢弃整场内存会话，不对外提供部分结果。
        时间语义：按 MVP 固定扣除 seconds_per_question，不使用实际输入耗时。
        异常：评价、仓库或响应生成错误向上传播；本方法不重发答案或重试整轮。
        """
        question = self.action.question
        answer = CandidateAnswer(
            interview_id=self.interview_id,
            question_id=question.question_id,
            answer_id=str(uuid4()),
            text=command.answer_text,
        )
        feedback = await self.app.evaluation.evaluate(
            EvaluationRequest(
                request_id=str(command.request_id),
                interview_id=self.interview_id,
                question=question,
                answer=answer,
            )
        )
        self.history.append(
            {
                "question_id": question.question_id,
                "competency": question.target_competency.value,
                "project_id": question.project_id,
                "topic": question.topic,
                "difficulty": question.difficulty,
                "question": question.text,
                "answer": answer.text,
                "evaluation": feedback.model_dump(
                    mode="json",
                    include={
                        "answer_relevance",
                        "evidence_strength",
                        "evaluation_confidence",
                        "rubric_level",
                        "needs_clarification",
                        "contradiction_detected",
                        "evidence_ids",
                    },
                ),
            }
        )
        self.action = await self.service.apply_evaluation_feedback(
            self.interview_id,
            feedback,
            elapsed_seconds=self.app.seconds_per_question,
        )
        return await self._response()

    async def _response(self):
        """将 Agent 动作规范化为网络可序列化的 question 或 finished 字典。

        逻辑：复用 MVP 的阶段推进方法，读取已提交状态；问题分支附最近评价，结束分支构建报告。
        返回：question 含完整问题、状态及 last_evaluation；finished.result 与终端 MVP 字段一致。
        依赖：调用 MVP 的内部阶段辅助方法，升级该接口时须同步核对本适配器和一致性测试。
        异常：无文本的问题或非预期动作抛 RuntimeError；其他错误保留原传播方式。
        报告生成器沿用 MVP 的确定性评分与文字备用逻辑，本层不增加备用输出。
        """
        self.action = await self.app._advance_non_question_actions(
            self.service, self.interview_id, self.action
        )
        context = await self.app.repository.get_interview_context(self.interview_id)
        if self.action.type == InterviewActionType.ASK_QUESTION:
            if self.action.question is None or not self.action.question.text:
                raise RuntimeError("Agent returned a question without text.")
            return {
                "type": "question",
                "interview_id": self.interview_id,
                "question": self.action.question.model_dump(mode="json"),
                "question_index": context.state.question_index,
                "interview_state": context.state.model_dump(mode="json"),
                "last_evaluation": self.history[-1]["evaluation"] if self.history else None,
            }
        if self.action.type != InterviewActionType.FINISH:
            raise RuntimeError("Agent returned an unexpected action.")
        report = await build_final_report(context, self.history, llm=self.llm)
        return {
            "type": "finished",
            "result": {
                "interview_id": self.interview_id,
                "candidate_name": self.candidate_name,
                "candidate_profile": self.profile.model_dump(mode="json"),
                "job_profile": self.job.model_dump(mode="json"),
                "topics": [project.name for project in self.profile.projects],
                "question_history": self.history,
                "interview_state": context.state.model_dump(mode="json"),
                "decision_logs": [
                    log.model_dump(mode="json")
                    for log in self.app.repository.decision_logs_for(self.interview_id)
                ],
                "interview_finished": context.state.status == "finished",
                "final_report": report.model_dump(mode="json"),
            },
        }

    def close(self):
        """向支持 close 的模型实例移交清理请求，不主动清空仍被后台调用引用的状态。

        前置条件：协议层已取消并等待本地异步任务；同步 SDK 请求可能仍在工作线程中。
        BackendLLM 负责延迟释放在途客户端；测试替身可只记录关闭标志。
        返回 None；会话对象随连接任务退出失去引用，close 的异常保持向上传播。
        """
        if hasattr(self.llm, "close"):
            self.llm.close()
