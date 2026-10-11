"""Responsibilities: Assemble the backend language-model provider used by Agent sessions.
Implementation: Reuse the MVP invocation implementation and obtain configuration only from the
backend environment.
Related Modules: agent_session composes this adapter; recommendation.rerank reuses its configuration
for a single fine-rank request.

Declaration Index:
- BackendLLM:
  Reuses structured output logic for OpenAI, Qwen and DeepSeek without logging inputs or keys.
- BackendLLM.__init__:
  Establishes synchronous model client using loaded backend environment, without sending inference
  requests.
- BackendLLM.__call__:
  Protects client lifecycle with counters and delegates one structured model invocation to MVP.
- BackendLLM.close:
  Marks refusal of new calls and releases synchronous client when no ongoing calls remain.

Variable Index:
- logger:
  Console logging entry for current module; context identifiers and exception handling methods
  detailed in respective functions.

Key State Notes:
BackendLLM._lock protects _active and _closing; _active counts ongoing calls.
_closing blocks new calls, released by last ongoing call upon completion.
provider/model/options control original MVP inference parameters; interview_id only used for logging
correlation.
request_timeout/resume_timeout reuse shared configuration budgets for generation and resume
extraction, selected by parent class based on call type.
capacity_lease passed from ASGI admission; synchronous calls borrow it, ensuring the capacity slot
is not released if remote call hasn’t returned after disconnection.
"""

import logging
import os
import threading
from time import perf_counter

from openai import OpenAI

from agents.config import load_agent_settings
from app.providers.llm import LLMError, OpenAILLM

logger = logging.getLogger(__name__)


class BackendLLM(OpenAILLM):
    """Reuse OpenAI/Qwen/DeepSeek structured output and log metadata without inputs or keys.
    """

    def __init__(self, *, interview_id=None):
        """Establishes synchronous model client using already-loaded backend environment, without
        sending inference requests.

        Input: Optional interview_id, used only to correlate model logs with session logs, not
        involved in prompt or scoring.
        Logic: Validate provider, corresponding key, and model name → read inference options →
        establish SDK → initialize concurrency counter.
        Dependencies: Django has already loaded root .env; skip parent constructor to avoid
        redundant loading, reusing its invocation implementation.
        Parameters: Reuse Agent’s generation and resume extraction timeout budgets; parent class
        selects budget based on call context,
        constrained by remaining deadline. Disable SDK auto-retry; structured repair handled by
        calling layer.
        Exceptions: Missing configuration raises LLMError; temperature conversion or SDK parameter
        errors propagated directly, for unified protocol-layer handling.
        """
        self.interview_id = interview_id
        self.provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        if self.provider not in {"openai", "dashscope", "deepseek"}:
            raise LLMError("Set LLM_PROVIDER to openai, dashscope or deepseek in root .env.")
        prefix = self.provider.upper()
        key = os.getenv(f"{prefix}_API_KEY", "").strip()
        self.model = os.getenv(f"{prefix}_MODEL", "").strip()
        if not key or not self.model:
            raise LLMError(f"Set {prefix}_API_KEY and {prefix}_MODEL in repository-root .env.")
        temperature = os.getenv("OPENAI_TEMPERATURE", "0").strip()
        self.options = {"temperature": float(temperature)} if temperature else {}
        client_options = {}
        if self.provider == "dashscope":
            client_options["base_url"] = os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
            ).strip()
        elif self.provider == "deepseek":
            client_options["base_url"] = os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).strip()
        timeouts = load_agent_settings().timeouts
        self.request_timeout = timeouts.llm_generation_seconds
        self.resume_timeout = timeouts.resume_extraction_seconds
        self.client = OpenAI(
            api_key=key, timeout=self.request_timeout, max_retries=0, **client_options
        )
        self._lock = threading.Lock()
        self._active = 0
        self._closing = False
        self.capacity_lease = None

    def __call__(self, prompt, data, schema):
        """Protects client lifecycle with counters and delegates one structured model invocation to
        MVP.

        Input: prompt is task instruction, data is candidate data, schema is expected Pydantic
        output model.
        Return: Schema instance after parent validation; original exception recorded with type and
        status code, then re-raised.
        Concurrency invariant: _active counts calls that have entered but not exited; _closing
        rejects new call entry.
        Lock only protects counters and closing state, not network waits, avoiding blocking entire
        request during thread cancellation.
        Log includes interview ID, model, schema, duration, text length, and whitespace token count;
        does not record full text or keys.
        repair_errors accepts only known error codes; does not re-validate or alter Agent’s
        generation rules here.
        Logging setup, parent call, or result processing exceptions are handled in finally block to
        restore ongoing count and borrowed service quota.
        """
        with self._lock:
            if self._closing:
                raise LLMError("Interview connection has closed.")
            lease = self.capacity_lease.retain() if self.capacity_lease is not None else None
            self._active += 1
        started = perf_counter()
        try:
            repair_codes = []
            for code in data.get("repair_errors") or ():
                if code in {
                    "EMPTY_TEXT",
                    "TOO_SHORT",
                    "TOO_LONG",
                    "MULTIPLE_PRIMARY_QUESTIONS",
                    "RUBRIC_OR_EXPECTED_ANSWER_LEAK",
                    "INVALID_DIFFICULTY",
                }:
                    repair_codes.append(code)
                elif isinstance(code, str) and code.startswith("GENERATION_ERROR:"):
                    repair_codes.append("GENERATION_ERROR")
            logger.info(
                "Agent model call interview=%s provider=%s schema=%s model=%s repair_codes=%s",
                self.interview_id,
                self.provider,
                schema.__name__,
                self.model,
                repair_codes,
            )
            result = super().__call__(prompt, data, schema)
            logger.info(
                "Agent model completed interview=%s schema=%s duration_ms=%d chars=%d words=%d",
                self.interview_id,
                schema.__name__,
                (perf_counter() - started) * 1000,
                len(getattr(result, "text", "")),
                len(getattr(result, "text", "").split()),
            )
            return result
        except Exception as exc:
            cause = exc.__cause__ or exc
            logger.error(
                "Agent model failed interview=%s schema=%s exception=%s status=%s duration_ms=%d; "
                "check repository-root .env, network, quota and structured-output support",
                self.interview_id,
                schema.__name__,
                type(cause).__name__,
                getattr(cause, "status_code", None),
                (perf_counter() - started) * 1000,
            )
            raise
        finally:
            # Even if parent class fails, return the count; the last ongoing call handles the prior
            # close request.
            try:
                with self._lock:
                    self._active -= 1
                    if self._closing and not self._active:
                        self.client.close()
            finally:
                if lease is not None:
                    lease.release()

    def close(self):
        """Marks refusal of new calls and releases synchronous client when no ongoing calls remain.

        Method: Check idempotent flag and active count under same lock to prevent race between close
        and new call entry.
        With ongoing calls, only set _closing; last call’s finally block releases client; no wait
        for its return.
        Returns None; does not guarantee cancellation of issued remote requests; SDK close
        exceptions are not silently swallowed.
        """
        with self._lock:
            if self._closing:
                return
            self._closing = True
            if not self._active:
                self.client.close()
