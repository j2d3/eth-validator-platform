import assert from "node:assert/strict";
import test from "node:test";
import {
  assertCanonicalOrigin,
  DEFAULT_CANONICAL_ORIGIN,
} from "../lib/canonical-origin-validator.mjs";

test("default canonical origin is the reference deployment", () => {
  assert.equal(DEFAULT_CANONICAL_ORIGIN, "https://g.j2d3.com");
});

test("accepts a bare HTTPS origin and returns url.origin", () => {
  assert.equal(
    assertCanonicalOrigin("https://portal.example.org"),
    "https://portal.example.org",
  );
});

test("accepts an HTTPS origin with an explicit trailing slash", () => {
  assert.equal(
    assertCanonicalOrigin("https://portal.example.org/"),
    "https://portal.example.org",
  );
});

test("rejects an HTTPS origin with an explicit non-default port", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org:8443"),
    /must not include an explicit port; got :8443/i,
  );
});

test("rejects an HTTPS origin with an explicit :443 (default port for scheme)", () => {
  // WHATWG URL parsing strips default ports so `new URL('https://foo:443').origin`
  // returns `https://foo` — the raw-string inspection must catch this before parsing.
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org:443"),
    /must not include an explicit port; got :443/i,
  );
});

test("rejects an http:// origin", () => {
  assert.throws(
    () => assertCanonicalOrigin("http://portal.example.org"),
    /must use https/i,
  );
});

test("rejects a URL with a username", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://alice@portal.example.org"),
    /must not include a username or password/i,
  );
});

test("rejects a URL with a username and password", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://alice:s3cret@portal.example.org"),
    /must not include a username or password/i,
  );
});

test("rejects a URL with a non-root path", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org/api"),
    /must not include a path/i,
  );
});

test("rejects a URL with a query string", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org/?mode=read"),
    /must not include a query string/i,
  );
});

test("rejects a URL with a fragment", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org/#top"),
    /must not include a fragment/i,
  );
});

test("rejects a syntactically invalid URL", () => {
  // Avoid whitespace here; a value like "not a url" would be rejected by the
  // earlier ASCII-whitespace guard before URL parsing runs. Use an input that
  // survives that check but still fails `new URL()`.
  assert.throws(
    () => assertCanonicalOrigin("not-a-url"),
    /not a valid URL/i,
  );
});

test("rejects an empty string", () => {
  assert.throws(
    () => assertCanonicalOrigin(""),
    /non-empty string/i,
  );
});

test("rejects a non-string value", () => {
  assert.throws(
    () => assertCanonicalOrigin(null),
    /non-empty string/i,
  );
  assert.throws(
    () => assertCanonicalOrigin(undefined),
    /non-empty string/i,
  );
});

// Adversarial regression tests for the whitespace/backslash bypass that
// slipped past the earlier raw-port check.

test("rejects leading whitespace (which WHATWG would trim before parsing)", () => {
  assert.throws(
    () => assertCanonicalOrigin(" https://portal.example.org"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects trailing whitespace (which WHATWG would trim before parsing)", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org "),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects leading whitespace hiding an explicit :443", () => {
  assert.throws(
    () => assertCanonicalOrigin(" https://portal.example.org:443"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects trailing whitespace hiding an explicit :443", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org:443 "),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects an embedded newline in the raw input", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org\n"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects a tab in the raw input", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org\t"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects a trailing backslash (which WHATWG would parse as '/')", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org\\"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects a trailing backslash hiding an explicit :443", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org:443\\"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects a NUL character in the raw input", () => {
  assert.throws(
    () => assertCanonicalOrigin("https://portal.example.org\x00"),
    /must not contain ASCII control characters, whitespace, or backslashes/i,
  );
});

test("rejects mixed-case scheme even if it parses (belt-and-braces raw==origin check)", () => {
  // `new URL('HTTPS://foo.com')` normalizes scheme to lowercase; the raw
  // input no longer equals the normalized origin, so the final check catches it.
  assert.throws(
    () => assertCanonicalOrigin("HTTPS://portal.example.org"),
    /must be exactly the normalized origin/i,
  );
});
