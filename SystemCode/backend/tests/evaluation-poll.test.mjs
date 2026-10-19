import test from "node:test";
import assert from "node:assert/strict";
import { pollEvaluationReport } from "../frontend/evaluation-poll.js";

test("polling reads history and stops at completion without posting", async () => {
  let callback, calls = 0; const values = [];
  pollEvaluationReport({ interviewId: "one", schedule: fn => { callback = fn; return 1; }, cancel() {},
    fetchImpl: async (url, options) => { assert.equal(options.method, undefined); calls++; return {
      ok: true, json: async () => ({ evaluation_status: calls === 1 ? "running" : "completed" }) }; },
    onUpdate: value => values.push(value), onError: assert.fail });
  await callback(); await callback(); await callback();
  assert.equal(calls, 2); assert.equal(values.length, 2);
});

test("late response after closing the report cannot overwrite another interview", async () => {
  let callback, resolve, updates = 0;
  const stop = pollEvaluationReport({ interviewId: "old", schedule: fn => { callback = fn; }, cancel() {},
    fetchImpl: () => new Promise(r => { resolve = r; }), onUpdate: () => updates++, onError: assert.fail });
  const pending = callback(); stop();
  resolve({ ok: true, json: async () => ({ evaluation_status: "completed" }) });
  await pending; assert.equal(updates, 0);
});

function probe(fetchImpl, options = {}) {
  const queued = [], updates = [], errors = [], deadlines = new Map();
  let sequence = 0;
  const stop = pollEvaluationReport({ interviewId: "one", fetchImpl,
    schedule: (fn, delay) => { queued.push({ fn, delay }); return queued.length; }, cancel() {},
    setDeadline: fn => { deadlines.set(++sequence, fn); return sequence; },
    clearDeadline: id => deadlines.delete(id),
    onUpdate: item => updates.push(item), onError: error => errors.push(error), ...options });
  return { queued, updates, errors, deadlines, stop, tick: () => queued.shift().fn() };
}
const complete = () => ({ ok: true, json: async () => ({ evaluation_status: "completed" }) });

test("temporary HTTP and network failures recover with bounded backoff and GET only", async () => {
  let calls = 0;
  const p = probe(async (url, options) => {
    assert.equal(options.method, undefined);
    calls++;
    if (calls === 1) return { ok: false, status: 503 };
    if (calls === 2) throw new TypeError("offline");
    return complete();
  });
  await p.tick(); assert.equal(p.queued[0].delay, 2000);
  await p.tick(); assert.equal(p.queued[0].delay, 4000);
  await p.tick();
  assert.equal(calls, 3); assert.equal(p.updates.length, 1);
  assert.equal(p.errors.length, 0); assert.equal(p.queued.length, 0);
});

test("authentication and missing report errors do not retry", async () => {
  for (const status of [401, 403, 404]) {
    const p = probe(async () => ({ ok: false, status }));
    await p.tick();
    assert.equal(p.queued.length, 0); assert.equal(p.errors.length, 1);
  }
});

test("persistent failures stop after three retries and retain the total polling limit", async () => {
  for (const [maxPolls, expected] of [[120, 4], [2, 2]]) {
    let calls = 0;
    const p = probe(async () => { calls++; return { ok: false, status: 429 }; }, { maxPolls });
    while (p.queued.length) await p.tick();
    assert.equal(calls, expected); assert.equal(p.errors.length, 1);
    assert.equal(p.deadlines.size, 0);
  }
});

test("a hung response body times out, aborts, and can recover without accepting late data", async () => {
  let calls = 0, firstSignal, resolve;
  const p = probe(async (url, options) => {
    if (++calls === 1) {
      firstSignal = options.signal;
      return { ok: true, json: () => new Promise(r => { resolve = r; }) };
    }
    return complete();
  });
  const pending = p.tick();
  await Promise.resolve();
  [...p.deadlines.values()][0]();
  await pending;
  assert.equal(firstSignal.aborted, true);
  await p.tick();
  resolve({ evaluation_status: "running" }); await Promise.resolve();
  assert.equal(p.updates.length, 1); assert.equal(p.updates[0].evaluation_status, "completed");
  assert.equal(p.errors.length, 0); assert.equal(p.deadlines.size, 0);
});

test("closing a report aborts an active fetch and suppresses retries and errors", async () => {
  let signal;
  const p = probe(async (url, options) => { signal = options.signal; return new Promise(() => {}); });
  const pending = p.tick(); p.stop(); await pending;
  assert.equal(signal.aborted, true); assert.equal(p.deadlines.size, 0);
  assert.equal(p.errors.length, 0); assert.equal(p.queued.length, 0);
});

test("a connection lost while reading the response body is retried", async () => {
  let calls = 0;
  const p = probe(async () => ++calls === 1
    ? { ok: true, json: async () => { throw new TypeError("connection reset"); } }
    : complete());
  await p.tick(); await p.tick();
  assert.equal(calls, 2); assert.equal(p.updates.length, 1); assert.equal(p.errors.length, 0);
});

test("a successful pending response resets only consecutive errors", async () => {
  let calls = 0;
  const p = probe(async () => {
    calls++;
    if ([1, 3].includes(calls)) return { ok: false, status: 502 };
    return { ok: true, json: async () => ({ evaluation_status: calls === 4 ? "completed" : "pending" }) };
  }, { maxRetries: 1 });
  while (p.queued.length) await p.tick();
  assert.equal(calls, 4); assert.equal(p.errors.length, 0); assert.equal(p.updates.length, 2);
});
