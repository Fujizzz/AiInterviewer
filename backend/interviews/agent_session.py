"""将 MVP 用例拆为可等待用户输入的网络会话，决策与评分仍由现有核心负责。

实现：可选预解析只缓存本连接的候选人资料；阶段事件包围真实 await，评分先于报告文字发送。
关联：agent_socket 设置请求级 emit_event；app 提供解析、评价和报告，Agent 保持决策顺序。

目录：
- AgentSession：
  保存一次连接的应用组合和已完成回答；不使用 Django 业务数据库。
- AgentSession.__init__：
  构造一次连接独占的 MVP 应用组合，不启动面试或调用模型。
- AgentSession.start：
  从已验证的 Start 命令初始化候选人、岗位及 Agent，再返回首个可展示动作。
- AgentSession.prepare：
  解析或复用当前连接内完全相同的简历文本，返回可供预览的资料，不消耗题目预算。
- AgentSession._stage：
  围绕真实异步阶段发送进度并记录完成、取消和失败耗时，不修改超时或重试。
- AgentSession._emit：
  仅向明确启用事件的请求发送通知，网络失败按原异常路径传播。
- AgentSession.answer：
  将当前答案转成标准反馈，驱动一次 Agent 决策并返回下一题或最终报告。
- AgentSession._response：
  将 Agent 动作规范化为网络可序列化的 question 或 finished 字典。
- AgentSession._response.observe_report：
  观察报告模型是否抛错并原样传播给既有报告函数，明确标记原有摘要回退，不新增回退。
- AgentSession.close：
  向支持 close 的模型实例移交清理请求，不主动清空仍被后台调用引用的状态。

关键变量：
- logger：
  记录阶段名称、面试标识、耗时和异常类型，不记录简历、回答或模型正文。

关键状态说明：
AgentSession.interview_id 为会话标识；app 持有本会话仓库与适配器。
history 为展示历史；action 为最近提交的 Agent 动作。
profile、candidate_name 在 prepare 中建立；prepared_text 仅用于本连接精确匹配。
job、service 在 start 中建立；emit_event 是当前请求的异步回调或 None。
所有缓存随连接关闭释放，解析失败不会被当作成功资料复用。
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from time import perf_counter
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

logger = logging.getLogger(__name__)


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
        self.prepared_text = None
        self.emit_event = None

    async def _emit(self, data):
        """输入为服务端事件字典；仅调用当前请求的 emit_event，返回 None，发送失败不吞掉。"""
        if self.emit_event is not None:
            await self.emit_event(data)

    @asynccontextmanager
    async def _stage(self, name):
        """输入固定阶段名；发送进入/完成事件并测量实际耗时，yield 不提供业务值。

        读取本会话 ID 和当前事件回调；异常及取消均记录类型后原样传播。
        此计时只用于观测，不参与 Agent 的逻辑时间预算，也不触发自动重试。
        """
        started = perf_counter()
        await self._emit({"type": "progress", "stage": name, "state": "running"})
        logger.info("Agent stage started interview=%s stage=%s", self.interview_id, name)
        try:
            yield
        except (Exception, asyncio.CancelledError) as exc:
            logger.warning(
                "Agent stage stopped interview=%s stage=%s duration_ms=%d exception=%s",
                self.interview_id,
                name,
                (perf_counter() - started) * 1000,
                type(exc).__name__,
            )
            raise
        else:
            elapsed = round((perf_counter() - started) * 1000)
            logger.info(
                "Agent stage completed interview=%s stage=%s duration_ms=%d",
                self.interview_id,
                name,
                elapsed,
            )
            await self._emit(
                {"type": "progress", "stage": name, "state": "completed", "duration_ms": elapsed}
            )

    async def prepare(self, command):
        """输入命令的 resume_text，解析为共享资料并返回 prepared 预览；不初始化 Agent。

        同一连接内文本完全相同才复用；更换文本先使旧缓存失效，再调用既有解析器。
        模型异常保持传播；不自动解析输入中的每次编辑，也不存文件或数据库。
        """
        if self.prepared_text != command.resume_text:
            self.prepared_text = None
            async with self._stage("resume_parsing"):
                self.profile, self.candidate_name = await parse_resume_profile(
                    command.resume_text, llm=self.llm, candidate_id=f"candidate-{self.interview_id}"
                )
            self.prepared_text = command.resume_text
        else:
            logger.info("Agent resume reused interview=%s", self.interview_id)
        return {"type": "prepared", "candidate_profile": self.profile.model_dump(mode="json")}

    async def start(self, command):
        """从已验证的 Start 命令初始化候选人、岗位及 Agent，再返回首个可展示动作。

        前置条件：协议层保证本连接尚未开始；参数类型和范围已通过 Start 校验。
        逻辑：精确复用或解析简历→构造岗位→装配端口→初始化计划→规范化阶段转换。
        预算：沿用 MVP 的题数乘以每题 120 秒，追问上限来自命令，能力权重不变。
        返回：question 响应字典，或 Agent 直接结束时的 finished 响应字典。
        副作用：调用模型并写入本会话内存；异常向协议层传播，失败会话不恢复。
        """
        await self.prepare(command)
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
        async with self._stage("question_generation"):
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
        async with self._stage("answer_evaluation"):
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
        async with self._stage("next_action"):
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
        报告生成器沿用 MVP 的评分与文字备用逻辑；启用事件时先发送仅含确定性数值的
        assessment，再生成报告文字，assessment 不表示文字报告已成功或可恢复会话。
        报告调用观察器只记录原有回退状态，不修改 prompt、schema、数值或异常捕获边界。
        """
        self.action = await self.app._advance_non_question_actions(
            self.service, self.interview_id, self.action
        )
        context = await self.app.repository.get_interview_context(self.interview_id)
        if self.action.type == InterviewActionType.ASK_QUESTION:
            if self.action.question is None or not self.action.question.text:
                raise RuntimeError("Agent returned a question without text.")
            details = self.action.decision_trace.details
            logger.info(
                "Agent question ready interview=%s question_index=%d generation_reason=%s "
                "generation_ms=%s planner_ms=%s retrieval_ms=%s",
                self.interview_id,
                context.state.question_index,
                details.get("generation_reason"),
                details.get("generation_latency_ms"),
                details.get("planner_latency_ms"),
                details.get("retrieval_latency_ms"),
            )
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
        if self.emit_event is not None:
            numeric = await build_final_report(context, self.history, llm=None)
            await self._emit(
                {
                    "type": "assessment",
                    "assessment": numeric.model_dump(
                        mode="json", include={"overall_score", "competencies"}
                    ),
                }
            )
        narrative_status = "completed"

        def observe_report(prompt, data, schema):
            """输入报告函数的原始模型参数，返回原模型结果；抛错时标记 fallback 并原样重抛。

            捕获 Exception 与现有 build_final_report 的叙述回退边界相同，调用在其工作线程中，
            返回前已完成本标记写入；取消后不发送事件或追加模型调用，不记录异常正文。
            """
            nonlocal narrative_status
            try:
                return self.llm(prompt, data, schema)
            except Exception as exc:
                narrative_status = "fallback"
                logger.warning(
                    "Agent report narrative failed interview=%s exception=%s; "
                    "existing deterministic summary will be used",
                    self.interview_id,
                    type(exc).__name__,
                )
                raise

        async with self._stage("report_generation"):
            report = await build_final_report(context, self.history, llm=observe_report)
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
                "report_narrative_status": narrative_status,
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
