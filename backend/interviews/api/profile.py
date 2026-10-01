"""职责：提供个人中心的本人基本资料读取和维护，复用 Django 用户表，无额外资料模型。
实现：session 认证、CSRF 和白名单字段；PATCH 仅更新提交的姓名/邮箱，账号身份与权限只读。
关联：api.urls 注册 /api/profile/；resumes.html/resumes.js 显示并维护基本资料。

目录：
- ProfileSerializer：本人资料序列化及有限字段校验。
- ProfileSerializer.Meta：公开身份字段与姓名/邮箱编辑字段。
- ProfileSerializer.validate：拒绝未知字段和只读字段写入。
- profile：认证 GET/PATCH，本人读取或更新并返回禁止缓存的响应。

关键变量：
- logger：仅记录用户 ID 和更新字段名称，不记录姓名、邮箱或会话信息。

配置说明：
ProfileSerializer.Meta.model 使用 Django 用户；fields 为公开字段白名单，read_only_fields
禁止修改 ID/用户名/注册日期，extra_kwargs 保持模型字段长度并允许姓名/邮箱为空。
约束：first_name 用作个人中心姓名；邮箱仅为联系方式，不作为已验证邮箱或登录凭据。
"""
import logging

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class ProfileSerializer(serializers.ModelSerializer):
    """功能：本人基本资料契约；逻辑：模型长度/邮箱格式校验；约束：不暴露密码和权限字段。"""

    class Meta:
        """功能：固定资料字段；逻辑：身份只读、姓名和邮箱可空；约束：不新增模型或验证状态。"""
        model = get_user_model()
        fields = ["id", "username", "first_name", "email", "date_joined"]
        read_only_fields = ["id", "username", "date_joined"]
        extra_kwargs = {
            "first_name": {"required": False, "allow_blank": True},
            "email": {"required": False, "allow_blank": True},
        }

    def validate(self, attrs):
        """输入模型校验后的字段；拒绝输入中任何非编辑字段，输出未额外改写的字典，无写入。"""
        if set(self.initial_data) - {"first_name", "email"}:
            raise serializers.ValidationError("Only first_name and email can be updated.")
        return attrs


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def profile(request):
    """输入真实认证请求；GET 返回本人资料，PATCH 校验后只更新提交字段并记录字段名。

    无副本用户 ID 输入；空 PATCH 不写库。格式错误由既有异常处理器返回 400，不部分保存。
    输出私有禁止缓存 JSON；姓名仅存 first_name，邮箱不触发验证邮件或修改认证方式。
    """
    serializer = ProfileSerializer(request.user)
    if request.method == "PATCH":
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = list(serializer.validated_data)
        if fields:
            for name, value in serializer.validated_data.items():
                setattr(request.user, name, value)
            request.user.save(update_fields=fields)
            logger.info("Profile updated owner=%s fields=%s", request.user.pk, sorted(fields))
    response = Response(serializer.data)
    response["Cache-Control"] = "no-store, private"
    response["X-Content-Type-Options"] = "nosniff"
    return response
