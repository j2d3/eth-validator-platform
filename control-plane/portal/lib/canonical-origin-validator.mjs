/**
 * Pure JavaScript validator for the portal's canonical origin.
 *
 * Lives in .mjs (not .ts) so Node's built-in test runner can import
 * and exercise it directly without going through the Vite build. The
 * .ts wrapper (`canonical-origin.ts`) applies this validator at
 * import time so a misconfigured environment fails the build rather
 * than producing a Worker that accepts unsafe traffic.
 *
 * A valid canonical origin is:
 *   - a syntactically valid URL,
 *   - with `https:` scheme,
 *   - with no username or password,
 *   - with no explicit port (including `:443` — WHATWG URL parsing
 *     strips default ports so raw-string inspection is required),
 *   - with no path (root `/` is allowed and is the URL default),
 *   - with no query string,
 *   - with no fragment.
 *
 * The return value is `url.origin` (normalized) so downstream
 * comparisons against `new URL(request.url).origin` are exact.
 */

export const DEFAULT_CANONICAL_ORIGIN = "https://g.j2d3.com";

export function assertCanonicalOrigin(raw) {
  if (typeof raw !== "string" || raw.length === 0) {
    throw new Error(
      "PORTAL_CANONICAL_ORIGIN must be a non-empty string",
    );
  }

  // Reject explicit ports (including :443) by inspecting the raw string
  // before URL parsing — WHATWG URL normalization strips default ports so
  // `new URL('https://foo.com:443').origin` returns `https://foo.com`.
  // The authority component is between `://` and the first `/`, `?`, `#`,
  // or end of string; userinfo (if present) precedes `@`.
  const rawAuthorityMatch = raw.match(/^https?:\/\/([^/?#]+)/i);
  if (rawAuthorityMatch) {
    const authority = rawAuthorityMatch[1];
    const hostAndPort = authority.includes("@")
      ? authority.slice(authority.lastIndexOf("@") + 1)
      : authority;
    // Ignore colons inside IPv6 brackets; only match `:PORT` after the
    // closing bracket (or after the plain hostname).
    const portMatch = /(?:\]|[^:\]]):(\d+)$/.exec(hostAndPort);
    if (portMatch) {
      throw new Error(
        `PORTAL_CANONICAL_ORIGIN must not include an explicit port; got :${portMatch[1]}`,
      );
    }
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error(
      `PORTAL_CANONICAL_ORIGIN is not a valid URL: ${JSON.stringify(raw)}`,
    );
  }

  if (parsed.protocol !== "https:") {
    throw new Error(
      `PORTAL_CANONICAL_ORIGIN must use https:; got ${parsed.protocol}`,
    );
  }
  if (parsed.username !== "" || parsed.password !== "") {
    throw new Error(
      "PORTAL_CANONICAL_ORIGIN must not include a username or password",
    );
  }
  if (parsed.pathname !== "/" && parsed.pathname !== "") {
    throw new Error(
      `PORTAL_CANONICAL_ORIGIN must not include a path; got ${JSON.stringify(parsed.pathname)}`,
    );
  }
  if (parsed.search !== "") {
    throw new Error(
      "PORTAL_CANONICAL_ORIGIN must not include a query string",
    );
  }
  if (parsed.hash !== "") {
    throw new Error(
      "PORTAL_CANONICAL_ORIGIN must not include a fragment",
    );
  }

  return parsed.origin;
}
