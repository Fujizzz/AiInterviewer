/**
 * @module avatar-configuration
 * Responsibilities: Load the authenticated backend's avatar connection settings.
 * Implementation: Make one bounded same-origin request, validate the response, and never retry
 * or open a player automatically. No model, audio or microphone calls are made.
 * Related Modules: avatar-url-policy.js validates the endpoint; interview and diagnostic pages
 * call this loader before connecting the official UE player.
 *
 * Declaration Index:
 * - loadAvatarConfiguration: Fetch and validate connection settings within one request deadline.
 * - loadAvatarConfiguration.callback1: Abort the configuration request when its deadline expires.
 *
 * Variable Index:
 * None
 */
import { validateAvatarConfiguration } from "./avatar-url-policy.js";

/**
 * Fetch settings once using the supplied fetch implementation, page URL, deadline and optional cancellation signal.
 * Include same-origin credentials, validate JSON through the URL policy and clear the deadline.
 * Propagate HTTP, parsing, policy and abort errors; do not retry or connect a player automatically.
 */
export async function loadAvatarConfiguration({
  fetchImpl = globalThis.fetch,
  pageUrl = globalThis.location.href,
  timeoutMs = 5000,
  signal,
} = {}) {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  const timer = setTimeout(/** Abort this request only when its configuration deadline expires. */ () => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl("/api/avatar/config/", {
      credentials: "same-origin", cache: "no-store", signal: controller.signal,
    });
    if (!response.ok) throw new Error(`Interviewer connection configuration failed (${response.status}).`);
    return validateAvatarConfiguration(await response.json(), pageUrl);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}
