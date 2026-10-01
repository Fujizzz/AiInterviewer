"""职责：为行为引擎、CLI 及模型传输测试提供固定场景、显式策略和严格请求。
实现：按原顺序读取已冻结数据，不连接业务数据库或外部模型，不修改标签或预算。
关联：test_behavior/test_cli/test_project_provider 共用夹具，生产不加载本文件。

目录：
- cases：读取原行为案例，保持原标签与顺序。
- policy：沿用行为回归的显式超时策略。
- behavior_request：构造攻击输入后正确拒绝的行为请求。

关键变量：
- DATA：固定行为案例文件路径。
"""

import json
from pathlib import Path

import pytest

from ai_security import SecurityPolicy
from shared.contracts.behavior import BehaviorRequest

DATA = Path(__file__).parent / "data/behavior_cases_v1.jsonl"


@pytest.fixture
def cases():
    """输入固定 DATA；返回原序案例；不修改标签、不作为模型效果盲测。"""
    return [
        json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


@pytest.fixture
def policy():
    """输入原行为策略快照；返回副本；保持既有 0.02 秒故障测试预算，不影响生产五秒预算。"""
    path = DATA.with_name("behavior_policy_v1.json")
    return SecurityPolicy.model_validate_json(path.read_text(encoding="utf-8")).model_copy(
        update={"semantic_timeout_seconds": 0.02}
    )


@pytest.fixture
def behavior_request(cases):
    """输入冻结案例；返回严格的合格行为请求，攻击文本作为证据而非拒绝标签。"""
    return BehaviorRequest.model_validate_json(json.dumps(cases[0]["request"]))
