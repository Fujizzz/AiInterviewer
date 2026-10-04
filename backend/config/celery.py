"""Responsibilities: Configure the background PDF task queue.
Implementation: Use Django-provided Redis addresses, pass random task identifiers in JSON, and
deliver content through short-lived Redis keys.
Related Modules: interviews.pdf_queue publishes tasks; interviews.tasks executes the existing PDF
pipeline.
Declaration Index:
None
Variable Index:
- app: Celery instance loaded by workers through config.celery:app.
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
