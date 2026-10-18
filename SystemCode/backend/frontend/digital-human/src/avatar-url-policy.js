/**
 * @module avatar-url-policy
 * Responsibilities: Validate avatar endpoints without network or device access.
 * Implementation: Permit loopback WS during local HTTP development and the same-origin
 * /ws/avatar/ WSS endpoint on HTTPS pages. Reject URL credentials and fragments.
 * Related Modules: avatar-configuration.js loads backend settings; pixel-player.js uses this
 * policy before opening a signalling connection.
 *
 * Declaration Index:
 * - validateAvatarSignallingUrl: Return a canonical URL or reject an unsafe page/endpoint pair.
 * - validateAvatarConfiguration: Validate enabled/settings types and apply signalling URL policy.
 *
 * Variable Index:
 * - LOOPBACK_HOSTS: Browser hostnames accepted for the existing local HTTP/WS demonstration.
 */

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost"]);

/**
 * Return a canonical signalling URL for the supplied endpoint and current page URL.
 * Accept loopback WS on loopback HTTP, or same-origin /ws/avatar/ WSS on HTTPS.
 * Throw on invalid URLs, credentials, fragments or unsupported origins; open no connection.
 */
export function validateAvatarSignallingUrl(value, pageUrl) {
  if (typeof value !== "string" || !value.trim()) throw new Error("Missing interviewer signalling URL.");
  const page = new URL(pageUrl);
  const endpoint = new URL(value);
  if (endpoint.username || endpoint.password || endpoint.href.includes("#")) {
    throw new Error("Interviewer signalling URLs cannot include credentials or fragments.");
  }
  if (page.protocol === "http:" && LOOPBACK_HOSTS.has(page.hostname)
      && endpoint.protocol === "ws:" && LOOPBACK_HOSTS.has(endpoint.hostname)) {
    return endpoint.href;
  }
  if (page.protocol === "https:" && endpoint.protocol === "wss:"
      && endpoint.host === page.host && endpoint.pathname === "/ws/avatar/") {
    return endpoint.href;
  }
  throw new Error("Use local WS on a local HTTP page, or the same-origin /ws/avatar/ WSS endpoint on HTTPS.");
}

/** Validate backend settings against the current page; disabled settings return no endpoint or fallback. */
export function validateAvatarConfiguration(value, pageUrl) {
  if (!value || typeof value.enabled !== "boolean" || typeof value.signalling_url !== "string") {
    throw new Error("The interviewer connection configuration is invalid.");
  }
  if (!value.enabled) return { enabled: false, signalling_url: "" };
  return { enabled: true, signalling_url: validateAvatarSignallingUrl(value.signalling_url, pageUrl) };
}
