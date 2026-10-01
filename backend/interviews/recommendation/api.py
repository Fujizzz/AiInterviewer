"""将两个推荐请求映射到固定模型服务。复用本机访问策略及统一DRF错误响应，不保存请求资料。

目录：
- validate_request：将Pydantic验证错误转换为不含原始输入的HTTP400。
- respond：执行排序并将特征错误/模型故障映射为400/503。
- recommend_jobs：候选人到岗位列表的POST入口。
- recommend_candidates：岗位到候选人列表的POST入口。

关键变量：
（无模块级变量。）

设计说明：
装饰器仅注册HTTP方法；输入契约在schemas，模型加载在runtime；无LLM调用或写库。
"""

from pydantic import ValidationError as SchemaError
from rest_framework.decorators import api_view
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response

from .runtime import ModelUnavailable, rank_pairs
from .schemas import CandidatesRequest, JobsRequest


def validate_request(schema, data):
    """功能：校验请求对象；输入schema类与解析后的JSON，输出验证对象。

    逻辑：错误只返回字段路径及错误类型，不返回输入值或上下文；约束：不吞掉非验证异常。
    """
    try:
        return schema.model_validate(data)
    except SchemaError as exc:
        errors = [
            {"field": ".".join(map(str, error["loc"])), "code": error["type"]}
            for error in exc.errors(include_input=False, include_context=False)
        ]
        raise ValidationError({"fields": errors}) from exc


def respond(pairs, direction):
    """功能：构造排序响应；输入已验证配对/方向，输出200 Response。

    逻辑：特征范围错误400，模型不可用503；约束：不重试、不回退、错误正文不含简历。
    """
    try:
        return Response(rank_pairs(pairs, direction))
    except ValueError as exc:
        raise ValidationError("Feature values are outside the supported range") from exc
    except ModelUnavailable as exc:
        error = APIException("Recommendation model unavailable; inspect server logs")
        error.status_code = 503
        error.default_code = "recommendation_unavailable"
        raise error from exc


@api_view(["POST"])
def recommend_jobs(request):
    """功能：按偏好分数排列岗位；输入一人和jobs列表，输出两分数及缺失资料信息；无持久化。"""
    data = validate_request(JobsRequest, request.data)
    return respond([(data.candidate, job) for job in data.jobs], "jobs")


@api_view(["POST"])
def recommend_candidates(request):
    """功能：按岗位侧合成赢家分数排列候选人；输入一岗和candidates列表，输出实验排序结果。"""
    data = validate_request(CandidatesRequest, request.data)
    return respond([(candidate, data.job) for candidate in data.candidates], "candidates")
