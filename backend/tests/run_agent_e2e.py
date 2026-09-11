"""启动离线模型替身的真实 ASGI 服务，检查文字面试及既有 HTTP/流式接口。

目录：
- check_agent：
  通过实际 WebSocket 完成两题面试，验证报告、关联 ID 与连接正常结束。

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


if __name__ == "__main__":
    main("interviews.tests.agent_fixture_app:application", check_agent)
