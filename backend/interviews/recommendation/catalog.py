"""职责：读取显式配置的岗位目录，为个人简历推荐提供展示信息和原模型要求。
实现：严格校验 JSON、唯一 ID 和既有 100 岗 HTTP 上限；不造岗位、不转换训练单位。
关联：schemas.JobInput 固定特征契约，resume_versions.recommendations 调用真实排序模型。
配置索引：CatalogJob.title/company/location/description 为展示文本，requirements 为原始模型要求；
JobCatalog.source_name/source_kind 标记来源，jobs 为完整待排序集合，不自动修改字段或采集岗位。
目录：
- CatalogUnavailable：目录缺失或无效的可诊断异常。
- CatalogJob：岗位展示元数据与独立的模型要求。
- JobCatalog：带来源标识的有界岗位集合。
- JobCatalog.unique_jobs：拒绝重复岗位 ID。
- load_catalog：只读配置文件，失败不切换其他来源。
关键变量：
- logger：仅记录配置状态、错误类型和数量，不记录目录正文或简历。
约束：
experience 表示体验数据；live 由管理员保证来源有效，不代表实时抓取。
"""

import logging
from pathlib import Path
from typing import Annotated, Literal

from django.conf import settings
from pydantic import Field, ValidationError, model_validator

from .schemas import MAX_ITEMS, JobInput, Profile, Text

logger = logging.getLogger(__name__)


class CatalogUnavailable(Exception):
    """功能：携带稳定故障码；逻辑：由加载边界抛出；约束：不包含敏感路径或原始数据。"""


class CatalogJob(Profile):
    """功能：声明岗位卡片和排序要求；输入严格 JSON，输出原样类型化字段。
    逻辑：展示字段不参与评分；约束：不从岗位描述推断要求，无数据库或网络副作用。
    """

    title: Text
    company: Text | None = None
    location: Text | None = None
    description: Annotated[str, Field(max_length=2000)] = ""
    requirements: JobInput


class JobCatalog(Profile):
    """功能：标识岗位来源；输入名称、类型及 1 至 100 岗，输出已验证目录。
    逻辑：遵守现有 HTTP 候选池大小；约束：超过上限报错，不静默截断或抽样。
    """

    source_name: Text
    source_kind: Literal["experience", "live"]
    jobs: Annotated[list[CatalogJob], Field(min_length=1, max_length=MAX_ITEMS)]

    @model_validator(mode="after")
    def unique_jobs(self):
        """输入验证实例，返回 self；重复 ID 抛 ValueError，避免排序结果关联到错误岗位。"""
        if len({job.requirements.job_id for job in self.jobs}) != len(self.jobs):
            raise ValueError("Duplicate job IDs")
        return self


def load_catalog():
    """输入 settings.RECOMMENDATION_JOB_CATALOG，返回严格 JobCatalog，不缓存或改写文件。
    空配置/读失败/非法目录抛稳定故障码；日志只保留异常类型，无重试、回退或自动联网。
    """
    configured = settings.RECOMMENDATION_JOB_CATALOG
    if not configured:
        logger.info("Job catalog unavailable reason=not_configured")
        raise CatalogUnavailable("job_catalog_not_configured")
    try:
        return JobCatalog.model_validate_json(Path(configured).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError) as exc:
        logger.error("Job catalog unavailable exception=%s", type(exc).__name__)
        raise CatalogUnavailable("job_catalog_invalid") from exc
