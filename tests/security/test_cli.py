"""职责：验证唯一行为检查 CLI 的契约、退出码、项目模型装配与脱敏错误。
实现：临时请求/策略文件和显式异步审查替身，不联网或读取真实模型配置。
关联：ai_security.__main__ 与真实面试网关共用 BehaviorEngine；不执行拟议业务动作。

目录：
- cli_files：写入临时冻结请求与原有测试策略，设置 CLI 参数。
- test_cli_decisions：完整合格、越界、检测失败分别返回既定退出码。
- test_cli_invalid_input：输入错误不创建模型，固定错误输出不含正文。
- test_cli_rejects_external_factory：拒绝外部 Python 工厂参数，不运行指定代码。

关键变量：
（无）
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_security import __main__ as cli
from shared.contracts.behavior import BehaviorAssessment


@pytest.fixture
def cli_files(tmp_path, monkeypatch, behavior_request, policy):
    """输入隔离目录和行为夹具；写入临时文件并设置参数；返回路径，不运行模型。"""
    request = tmp_path / "request.json"
    configuration = tmp_path / "policy.json"
    request.write_text(behavior_request.model_dump_json(), encoding="utf-8")
    configuration.write_text(policy.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["ai_security", "--request", str(request), "--policy", str(configuration)]
    )
    return request, configuration


@pytest.mark.parametrize("verdict,code", [("compliant", 0), ("noncompliant", 2), ("uncertain", 3)])
def test_cli_decisions(cli_files, monkeypatch, behavior_request, capsys, verdict, code):
    """输入固定行为结论；检查唯一模型工厂、真实引擎与退出码，不把替身当检测效果。"""
    required = next(
        p.requirement_ids
        for p in behavior_request.boundary.permits
        if p.operation == behavior_request.proposal.operation
    )
    reviewer = MagicMock()
    reviewer.assess = AsyncMock(
        return_value=BehaviorAssessment(
            verdict=verdict,
            checked_requirement_ids=required,
            violated_requirement_ids=(required[0],) if verdict == "noncompliant" else (),
        )
    )
    factory = MagicMock(return_value=reviewer)
    monkeypatch.setattr(cli, "create_behavior_reviewer", factory)
    assert cli.main() == code
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == {0: "allow", 2: "deny", 3: "error"}[code]
    factory.assert_called_once_with()
    reviewer.assess.assert_awaited_once()


def test_cli_invalid_input(cli_files, monkeypatch, capsys):
    """输入非法且含私密标记的 JSON；模型工厂不能调用，错误仅返回类型及固定代码。"""
    request, _ = cli_files
    request.write_text('{"PRIVATE_INPUT":true}', encoding="utf-8")
    factory = MagicMock()
    monkeypatch.setattr(cli, "create_behavior_reviewer", factory)
    assert cli.main() == 4
    factory.assert_not_called()
    output = capsys.readouterr().out
    assert "PRIVATE_INPUT" not in output
    assert json.loads(output)["error_code"] == "invalid_input_or_configuration"


def test_cli_rejects_external_factory(cli_files, monkeypatch):
    """输入已删除的外部工厂选项；参数解析直接失败，不创建项目或外部模型。"""
    request, configuration = cli_files
    monkeypatch.setattr(
        "sys.argv",
        [
            "ai_security",
            "--request",
            str(request),
            "--policy",
            str(configuration),
            "--reviewer-factory",
            "untrusted:factory",
        ],
    )
    factory = MagicMock()
    monkeypatch.setattr(cli, "create_behavior_reviewer", factory)
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    factory.assert_not_called()
