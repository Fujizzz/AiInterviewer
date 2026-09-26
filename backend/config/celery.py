"""职责：装配 PDF 后台任务队列，显式禁用业务重试与任务结果正文日志。

实现：Django 设置提供 Redis 地址；JSON 仅传递随机任务标识，正文经短期 Redis 键交付。
关联：interviews.pdf_queue 发布任务，interviews.tasks 执行既有 PDF 管线。
目录：
（无本地函数或类定义。）
关键变量：
- app：Celery 实例，worker 以 config.celery:app 启动。
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("ai_interviewer")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.update(
    imports=("interviews.tasks",),
    task_default_queue="resume_pdf",
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_store_errors_even_if_ignored=False,
    task_publish_retry=False,
    task_acks_late=False,
    task_reject_on_worker_lost=False,
    worker_prefetch_multiplier=1,
    broker_connection_retry=False,
    broker_connection_retry_on_startup=False,
    broker_transport_options={"max_retries": 0, "socket_timeout": 5},
    worker_hijack_root_logger=False,
)
