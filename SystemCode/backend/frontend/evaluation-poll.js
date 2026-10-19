/** Read-only report polling. Never submits answers, creates jobs, or invokes models. */
export function pollEvaluationReport({ interviewId, onUpdate, onError, fetchImpl = fetch,
  schedule = setTimeout, cancel = clearTimeout, maxPolls = 120, maxRetries = 3,
  requestTimeoutMs = 10000, setDeadline = setTimeout, clearDeadline = clearTimeout }) {
  let stopped = false, timer = null, count = 0, errors = 0, abortActive = null;
  const stop = () => {
    stopped = true;
    if (timer !== null) cancel(timer);
    if (abortActive) abortActive();
  };
  async function readDetail() {
    const controller = new AbortController();
    let deadline;
    const timeout = new Promise((_, reject) => {
      abortActive = () => { reject(new Error("report_request_cancelled")); controller.abort(); };
      deadline = setDeadline(() => {
        reject(Object.assign(new Error("report_request_timeout"), { retryable: true }));
        controller.abort();
      }, requestTimeoutMs);
    });
    const request = (async () => {
      let response;
      try {
        response = await fetchImpl(`/api/agent-interviews/${encodeURIComponent(interviewId)}/`,
          { credentials: "same-origin", cache: "no-store", signal: controller.signal });
      } catch (error) {
        throw Object.assign(new Error("report_network_error", { cause: error }), { retryable: true });
      }
      if (!response.ok) throw Object.assign(new Error(`HTTP ${response.status}`), {
        retryable: [408, 429].includes(response.status) || response.status >= 500,
      });
      try { return await response.json(); }
      catch (error) {
        // A connection can break while reading the body, after HTTP headers arrived.
        if (["TypeError", "AbortError"].includes(error.name)) error.retryable = true;
        throw error;
      }
    })();
    try { return await Promise.race([request, timeout]); }
    finally { clearDeadline(deadline); abortActive = null; }
  }
  async function poll() {
    if (stopped) return;
    timer = null;
    count++;
    try {
      const detail = await readDetail();
      if (stopped) return;
      onUpdate(detail);
      if (!["pending", "running", "idle"].includes(detail.evaluation_status)) { stop(); return; }
      errors = 0;
      if (count >= maxPolls) throw new Error("report_poll_limit");
      timer = schedule(poll, Math.min(10000, 2000 + count * 250));
    } catch (error) {
      if (stopped) return;
      if (error.retryable && ++errors <= maxRetries && count < maxPolls) {
        timer = schedule(poll, Math.min(10000, 2000 * (2 ** (errors - 1))));
      } else { stop(); onError(error); }
    }
  }
  timer = schedule(poll, 2000);
  return stop;
}
