"""REST 数据契约。严格验证可写字段、题目顺序和动作参数；响应包含不可变题目快照。

目录：
- StrictInputMixin
- StrictInputMixin.to_internal_value
- QuestionSerializer
- QuestionSerializer.Meta
- SessionQuestionSerializer
- SessionQuestionSerializer.Meta
- SessionSerializer
- SessionSerializer.Meta
- CreateSessionSerializer
- CreateSessionSerializer.validate_question_ids
- VersionSerializer
- ItemCommandSerializer
- ItemCommandSerializer.validate
"""

from rest_framework import serializers

from ..models import PracticeSession, Question, SessionQuestion


class StrictInputMixin:
    """严格输入策略：未知字段与只读字段一律拒绝，避免默默忽略调用错误。"""

    def to_internal_value(self, data):
        """功能：在 DRF 类型转换前验证字段白名单。
        方法：允许集合来自非只读字段；多余键组成字段级 ValidationError。
        返回：DRF 转换结果；非字典输入交给 DRF 原有类型校验。"""
        if isinstance(data, dict):
            allowed = {name for name, field in self.fields.items() if not field.read_only}
            unknown = set(data) - allowed
            if unknown:
                raise serializers.ValidationError(
                    {name: "Unknown or read-only field." for name in sorted(unknown)}
                )
        return super().to_internal_value(data)


class QuestionSerializer(StrictInputMixin, serializers.ModelSerializer):
    """题库读写契约，主键与创建/更新时间由服务端管理。"""

    class Meta:
        """声明外部字段及只读字段，保持题库写入范围可审计。"""

        model = Question
        fields = ["id", "text", "position", "enabled", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SessionQuestionSerializer(serializers.ModelSerializer):
    """单题只用于响应展示，包含题目快照、作答与服务端时间。"""

    class Meta:
        """显式列出单题响应字段，避免新增模型内部字段被自动暴露。"""

        model = SessionQuestion
        fields = [
            "id",
            "question_id",
            "question_text",
            "position",
            "status",
            "answer_text",
            "duration_ms",
            "started_at",
            "finished_at",
        ]


class SessionSerializer(serializers.ModelSerializer):
    """场次响应契约，按模型顺序嵌套只读单题列表。"""

    items = SessionQuestionSerializer(many=True, read_only=True)

    class Meta:
        """显式公开状态、版本、固定时长及单题快照。"""

        model = PracticeSession
        fields = [
            "id",
            "status",
            "version",
            "prep_seconds",
            "answer_seconds",
            "started_at",
            "finished_at",
            "items",
        ]


class CreateSessionSerializer(StrictInputMixin, serializers.Serializer):
    """创建请求契约：可选非空 UUID 序列，最多 100 项。"""

    question_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=100
    )

    def validate_question_ids(self, value):
        """用集合长度检查重复 ID；成功时原样返回列表，保留用户指定顺序。"""
        if len(set(value)) != len(value):
            raise serializers.ValidationError("Duplicate question IDs are not allowed.")
        return value


class VersionSerializer(StrictInputMixin, serializers.Serializer):
    """需要变更场次的请求基类，要求 version 为大于等于 1 的整数。"""

    version = serializers.IntegerField(min_value=1)


class ItemCommandSerializer(VersionSerializer):
    """单题动作契约，约束动作枚举、答案长度与可选时长范围。"""

    action = serializers.ChoiceField(choices=["start", "complete", "skip"])
    answer_text = serializers.CharField(
        required=False, allow_blank=True, max_length=20000, trim_whitespace=False
    )
    duration_ms = serializers.IntegerField(required=False, min_value=0, max_value=2147483647)

    def validate(self, attrs):
        """交叉验证动作与答案字段：只有 complete 接受答案与时长，其余组合明确拒绝。"""
        if attrs["action"] != "complete" and ({"answer_text", "duration_ms"} & attrs.keys()):
            raise serializers.ValidationError("Answer fields are accepted only for complete.")
        return attrs
