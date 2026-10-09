/**
 * @module avatar-configuration-tests
 * Responsibilities: Verify local/public endpoint policy and bounded authenticated configuration loading.
 * Implementation: Exercise pure URL validation and injected fetch fixtures; no browser, server or model is used.
 * Related Modules: avatar-url-policy.js rejects unsafe URLs; avatar-configuration.js loads approved settings.
 *
 * Declaration Index:
 * - callback1: Accept legacy loopback WS and the approved same-origin public WSS route.
 * - callback2: Reject mixed content, cross-origin endpoints and unsupported public routes.
 * - callback2.callback1: Invoke URL validation for each rejected page/endpoint fixture.
 * - callback3: Reject endpoint credentials and fragments, including an empty fragment.
 * - callback3.callback1: Validate each credential/fragment rejection fixture on HTTPS.
 * - callback3.callback2: Verify local WS endpoints also reject embedded credentials.
 * - callback4: Preserve disabled configuration and reject malformed response shapes.
 * - callback4.callback1: Validate each malformed configuration fixture.
 * - callback5: Verify one authenticated, uncached configuration request and its approved URL.
 * - callback5.object1.fetchImpl: Record request options and return an approved fake HTTP response.
 * - callback5.object1.fetchImpl.object2.json: Return the enabled same-origin settings fixture.
 * - callback6: Preserve HTTP/policy failures without retrying or inventing an endpoint.
 * - callback6.object1.fetchImpl: Count the one request and return an unauthorized HTTP fixture.
 * - callback6.object2.fetchImpl: Return settings with an endpoint forbidden on the public page.
 * - callback6.object2.fetchImpl.object1.json: Supply the unsafe loopback endpoint fixture.
 * - callback7: Verify the finite request deadline aborts a hung fetch fixture.
 * - callback7.object1.fetchImpl: Keep a fake fetch pending until its abort signal fires.
 * - callback7.object1.fetchImpl.callback1: Register rejection of the pending fake fetch on abort.
 * - callback7.object1.fetchImpl.callback1.callback1: Reject the fake request with AbortError.
 *
 * Variable Index:
 * - localPage: Loopback HTTP page fixture preserving the local demonstration connection policy.
 * - publicPage: HTTPS deployed-page fixture requiring the same-origin avatar signalling route.
 */
import test from "node:test";
import assert from "node:assert/strict";
import { validateAvatarSignallingUrl, validateAvatarConfiguration } from "../src/avatar-url-policy.js";
import { loadAvatarConfiguration } from "../src/avatar-configuration.js";

const localPage = "http://127.0.0.1:8765/agent/";
const publicPage = "https://47.239.50.129/agent/";

/** Pure URL fixtures preserve local WS and the approved public WSS route without opening connections. */
test("local HTTP retains loopback signalling while public HTTPS accepts only its avatar route", () => {
  assert.equal(validateAvatarSignallingUrl("ws://127.0.0.1:8889", localPage), "ws://127.0.0.1:8889/");
  assert.equal(validateAvatarSignallingUrl("ws://localhost:8889/", localPage), "ws://localhost:8889/");
  assert.equal(validateAvatarSignallingUrl("ws://127.0.0.1:8765/ws/avatar/", localPage), "ws://127.0.0.1:8765/ws/avatar/");
  assert.equal(validateAvatarSignallingUrl("wss://47.239.50.129/ws/avatar/", publicPage), "wss://47.239.50.129/ws/avatar/");
});

/** Pure URL fixtures reject insecure, cross-origin and wrong-path public endpoints. */
test("reject mixed content, cross-origin and non-avatar public routes without opening a connection", () => {
  for (const [endpoint, page] of [
    ["ws://127.0.0.1:8889", publicPage],
    ["ws://47.239.50.129/ws/avatar/", publicPage],
    ["wss://example.com/ws/avatar/", publicPage],
    ["wss://47.239.50.129:8443/ws/avatar/", publicPage],
    ["wss://47.239.50.129/ws/agent/", publicPage],
    ["wss://47.239.50.129/ws/avatar", publicPage],
    ["ws://127.0.0.1:8889", "http://example.com/agent/"],
    ["ws://example.com:8889/", localPage],
    ["wss://47.239.50.129/ws/avatar/", localPage],
  ]) /** Validate this rejected page/endpoint pair without network access. */ assert.throws(() => validateAvatarSignallingUrl(endpoint, page));
});

/** Pure URL fixtures reject embedded credentials and both nonempty and empty fragments. */
test("reject endpoint credentials and fragments including an empty fragment", () => {
  for (const endpoint of ["wss://user:secret@47.239.50.129/ws/avatar/", "wss://47.239.50.129/ws/avatar/#x", "wss://47.239.50.129/ws/avatar/#"]) {
    /** Validate this credential/fragment fixture against the public-page policy. */
    assert.throws(() => validateAvatarSignallingUrl(endpoint, publicPage), /credentials or fragments/);
  }
  /** Confirm the local demonstration also rejects URL credentials. */
  assert.throws(() => validateAvatarSignallingUrl("ws://user:secret@localhost:8889", localPage));
});

/** Response-shape fixtures keep disabled deployment disabled and reject invalid configuration values. */
test("disabled deployment does not invent a loopback fallback; malformed settings are rejected", () => {
  assert.deepEqual(validateAvatarConfiguration({ enabled: false, signalling_url: "" }, publicPage), { enabled: false, signalling_url: "" });
  for (const configuration of [null, {}, { enabled: "true", signalling_url: "ws://localhost" }, { enabled: true, signalling_url: "" }]) {
    /** Apply shape and URL validation to this malformed configuration fixture. */
    assert.throws(() => validateAvatarConfiguration(configuration, localPage));
  }
});

/** Inject one successful fetch response and assert request authentication/cache options and endpoint policy. */
test("configuration loader makes one same-origin authenticated request and validates its response", async () => {
  const requests = [];
  const configuration = await loadAvatarConfiguration({ pageUrl: publicPage,
    /** Capture the single request and return a successful, approved settings response. */
    fetchImpl: async (url, options) => {
    requests.push({ url, options });
    return { ok: true, /** Supply the enabled same-origin settings fixture. */ json: async () => ({ enabled: true, signalling_url: "wss://47.239.50.129/ws/avatar/" }) };
  } });
  assert.equal(configuration.signalling_url, "wss://47.239.50.129/ws/avatar/");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/avatar/config/");
  assert.equal(requests[0].options.credentials, "same-origin");
  assert.equal(requests[0].options.cache, "no-store");
});

/** Inject unauthorized and unsafe-endpoint responses; verify errors propagate without retry or fallback. */
test("configuration loader preserves HTTP and policy errors without retries or fallback", async () => {
  let requests = 0;
  await assert.rejects(loadAvatarConfiguration({ pageUrl: publicPage,
    /** Count the one attempted request and return an unauthorized response fixture. */
    fetchImpl: async () => {
    requests++; return { ok: false, status: 401 };
  } }), /401/);
  assert.equal(requests, 1);
  await assert.rejects(loadAvatarConfiguration({ pageUrl: publicPage,
    /** Return a successful HTTP response whose endpoint violates the deployed-page policy. */
    fetchImpl: async () => ({
    ok: true, /** Supply the unsafe loopback settings fixture. */ json: async () => ({ enabled: true, signalling_url: "ws://localhost:8889" }),
  }) }), /same-origin/);
});

/** Inject a fetch that only rejects on abort and verify a finite deadline releases the pending request. */
test("configuration loading has a finite deadline and cancels a hung request", async () => {
  await assert.rejects(loadAvatarConfiguration({ pageUrl: localPage, timeoutMs: 1,
    /** Keep this fake fetch pending until the configuration loader aborts its signal. */
    fetchImpl: (_url, { signal }) => new Promise(/** Reject the pending fixture when its abort signal fires. */ (_resolve, reject) => {
      /** Translate the fixture's abort event into the expected fetch AbortError. */
      signal.addEventListener("abort", () => reject(new DOMException("Timed out", "AbortError")), { once: true });
    }),
  }), { name: "AbortError" });
});
