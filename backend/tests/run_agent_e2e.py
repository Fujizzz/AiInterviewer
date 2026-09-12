"""启动离线模型替身的真实 ASGI 服务，检查文字面试及既有 HTTP/流式接口。

目录：
- check_agent：
  通过实际 WebSocket 完成两题面试，验证报告、关联 ID 与连接正常结束。
- check_progress：
  实际网络验证预解析复用、真实阶段和评分先行，不调用供应商。
- check_all：
  在同一隔离服务分别运行旧协议和新协议场景，保持既有回归断言。

关键变量：
（无模块级变量。）
"""

import json
import math
from uuid import uuid4

from run_e2e import main
from websockets.sync.client import connect


def check_agent(base):
    """通过实际 WebSocket 完成两题面试，验证报告、关联 ID 与连接正常结束。

    前置条件：base 必须指向显式注入 FixtureLLM 的测试入口，不能用于真实模型评分验证。
    方法：等待 hello→提交虚构简历→逐题回答→核对 finished、固定预算和离线评分。
    返回 None；异常或断言失败直接退出，上层启动器负责停止服务和清理临时数据库。
    """
    with connect(base.replace("http://", "ws://") + "/ws/agent/", origin=base, proxy=None) as ws:
        assert json.loads(ws.recv(timeout=5))["type"] == "hello"
        request_id = str(uuid4())
        ws.send(
            json.dumps(
                {
                    "type": "start",
                    "request_id": request_id,
                    "max_questions": 2,
                    "resume_text": "Alex built a Python log pipeline and tested malformed records.",
                }
            )
        )
        answered = 0
        while True:
            started = json.loads(ws.recv(timeout=5))
            assert started["type"] == "started" and started["request_id"] == request_id
            message = json.loads(ws.recv(timeout=5))
            assert message["request_id"] == request_id
            if message["type"] == "finished":
                assert message["result"]["interview_finished"]
                assert len(message["result"]["question_history"]) == answered == 2
                assert message["result"]["interview_state"]["elapsed_seconds"] == 240
                assert math.isclose(message["result"]["final_report"]["overall_score"], 3.0)
                break
            assert message["type"] == "question"
            answered += 1
            request_id = str(uuid4())
            ws.send(
                json.dumps(
                    {
                        "type": "answer",
                        "request_id": request_id,
                        "question_id": message["question"]["question_id"],
                        "answer_text": (
                            "I implemented a bounded-memory parser and tested malformed records."
                        ),
                    }
                )
            )
    print("PASS offline Agent over real WebSocket: two answers, MVP budget, evaluation and report")


def check_progress(base):
    """输入显式离线 ASGI 服务 URL；预解析后开启单题面试，验证进度顺序与数值一致。

    每个命令 UUID 唯一，事件必须关联当前请求；prepared 后 start 不应再次出现解析阶段。
    超时仅作为测试失败边界，不改变生产配置；收到评分后仍等待最终报告，无模型调用。
    """
    resume = "Alex built a Python log pipeline and tested malformed records."
    with connect(base.replace("http://", "ws://") + "/ws/agent/", origin=base, proxy=None) as ws:
        hello = json.loads(ws.recv(timeout=5))
        assert "prepare" in hello["capabilities"]
        rid = str(uuid4())
        ws.send(
            json.dumps(
                {
                    "type": "prepare",
                    "request_id": rid,
                    "resume_text": resume,
                    "progress_events": True,
                }
            )
        )
        preparing = []
        while True:
            message = json.loads(ws.recv(timeout=5))
            assert message["request_id"] == rid
            assert message["type"] in {"started", "progress", "prepared"}
            preparing.append(message)
            if message["type"] == "prepared":
                break
        assert [m["stage"] for m in preparing if m["type"] == "progress"] == [
            "resume_parsing",
            "resume_parsing",
        ]
        rid = str(uuid4())
        ws.send(
            json.dumps(
                {
                    "type": "start",
                    "request_id": rid,
                    "resume_text": resume,
                    "max_questions": 1,
                    "progress_events": True,
                }
            )
        )
        early_score = None
        answered = False
        while True:
            message = json.loads(ws.recv(timeout=5))
            assert message["request_id"] == rid
            if message["type"] == "progress":
                assert message["stage"] != "resume_parsing"
                if message["stage"] == "report_generation":
                    assert early_score is not None
            elif message["type"] == "question":
                assert not answered
                answered = True
                rid = str(uuid4())
                ws.send(
                    json.dumps(
                        {
                            "type": "answer",
                            "request_id": rid,
                            "question_id": message["question"]["question_id"],
                            "answer_text": "I implemented and tested the parser.",
                            "progress_events": True,
                        }
                    )
                )
            elif message["type"] == "assessment":
                early_score = message["assessment"]["overall_score"]
                assert math.isclose(early_score, 3.0)
            elif message["type"] == "finished":
                assert answered and early_score is not None
                assert message["result"]["final_report"]["overall_score"] == early_score
                assert message["result"]["report_narrative_status"] == "completed"
                assert message["result"]["interview_state"]["elapsed_seconds"] == 120
                break
            else:
                assert message["type"] == "started", message
    print("PASS real WebSocket preparation reuse, progress and assessment before narrative")


def check_all(base):
    """输入隔离服务 URL，依次执行旧协议和新事件协议；任意失败原样传播，不减少原断言。"""
    check_agent(base)
    check_progress(base)


if __name__ == "__main__":
    main("interviews.tests.agent_fixture_app:application", check_all)
