/**
 * Canonical origin for the portal, validated at import time.
 *
 * Reads PORTAL_CANONICAL_ORIGIN **at build time** so a fork can deploy
 * to its own domain without editing source. Vite `define` substitutes
 * `process.env.PORTAL_CANONICAL_ORIGIN` in this module with the
 * validated JSON-string literal at build time (see `vite.config.ts`);
 * the compiled Worker/RSC/SSR bundles carry that literal directly, so
 * setting or changing `process.env.PORTAL_CANONICAL_ORIGIN` at runtime
 * has no effect on a built artifact. Falls back to the
 * reference-deployment origin when the env var is unset at build
 * time, so unmodified `npm run build` and `npm test` still work.
 *
 * Validation lives in `canonical-origin-validator.mjs` (pure JS so
 * Node's test runner can exercise it directly). An invalid value
 * throws at Vite config-load time — the build fails rather than
 * producing a Worker that would accept unsafe traffic.
 *
 * Set the env var at BUILD time to change the value:
 *
 *   PORTAL_CANONICAL_ORIGIN="https://portal.example.org" npm run build
 */
import {
  assertCanonicalOrigin,
  DEFAULT_CANONICAL_ORIGIN,
} from "./canonical-origin-validator.mjs";

export const CANONICAL_ORIGIN = assertCanonicalOrigin(
  process.env.PORTAL_CANONICAL_ORIGIN ?? DEFAULT_CANONICAL_ORIGIN,
);

export const CANONICAL_URL = new URL(CANONICAL_ORIGIN);
export const CANONICAL_HOSTNAME = CANONICAL_URL.hostname;
