import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

// Reuse the same validator the Worker + layout use, so what the test
// asserts about canonical URLs is the same value the build baked in.
import {
  assertCanonicalOrigin,
  DEFAULT_CANONICAL_ORIGIN,
} from "../lib/canonical-origin-validator.mjs";

const CANONICAL_ORIGIN = assertCanonicalOrigin(
  process.env.PORTAL_CANONICAL_ORIGIN ?? DEFAULT_CANONICAL_ORIGIN,
);
const CANONICAL_HOSTNAME = new URL(CANONICAL_ORIGIN).hostname;
const escapeForRegex = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const CANONICAL_ORIGIN_RE = escapeForRegex(CANONICAL_ORIGIN);

async function requestPortal(url = `${CANONICAL_ORIGIN}/`, headers = {}) {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(url, {
      headers: { accept: "text/html", ...headers },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

async function render() {
  return requestPortal();
}

test("server-renders the project home and its safety posture", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Validator Platform — Field Console<\/title>/i);
  assert.match(
    html,
    new RegExp(`property="og:image" content="${CANONICAL_ORIGIN_RE}/og\\.png"`, "i"),
  );
  assert.match(
    html,
    new RegExp(`rel="canonical" href="${CANONICAL_ORIGIN_RE}/?"`, "i"),
  );
  assert.match(html, /name="twitter:card" content="summary_large_image"/i);
  assert.match(html, /One view of the system/);
  assert.match(html, /None of the comforting lies/);
  assert.match(html, /Ready is not authorized/);
  assert.match(html, /signing<\/span><strong>OFF/);
  assert.match(html, /Three answers, never one blended status/);
  assert.match(html, /Scan status belongs beside runtime health/);
  assert.match(html, /Vulnerability alerts/);
  assert.match(html, /Container image scanning/);
  assert.match(html, /The front door, not another replacement dashboard/);
  assert.match(html, /not a production staking service/i);
  assert.equal(
    response.headers.get("strict-transport-security"),
    "max-age=31536000; includeSubDomains",
  );
});

test("serves content only on the canonical HTTPS origin", async () => {
  for (const url of [
    `http://${CANONICAL_HOSTNAME}/path?mode=read`,
    "https://preview.invalid/path?mode=read",
    `https://${CANONICAL_HOSTNAME}:8443/path?mode=read`,
  ]) {
    const response = await requestPortal(url, {
      host: "attacker.invalid",
      "x-forwarded-host": "attacker.invalid",
      "x-forwarded-proto": "http",
    });
    assert.equal(response.status, 308);
    assert.equal(
      response.headers.get("location"),
      `${CANONICAL_ORIGIN}/path?mode=read`,
    );
  }

  const canonical = await requestPortal(`${CANONICAL_ORIGIN}/`, {
    "x-forwarded-host": "attacker.invalid",
  });
  const html = await canonical.text();
  assert.equal(canonical.status, 200);
  assert.match(
    html,
    new RegExp(`${CANONICAL_ORIGIN_RE}/og\\.png`, "i"),
  );
  assert.doesNotMatch(html, /attacker\.invalid/i);
});

test("redirect stays on canonical origin for a //host/path network-path reference", async () => {
  // Regression: `new URL(pathname + search, base)` treats a leading `//`
  // as a network-path reference and would produce an open redirect
  // off-canonical. The Worker must construct the destination by
  // assigning pathname/search onto a URL parsed from CANONICAL_ORIGIN.
  const response = await requestPortal(
    "https://preview.invalid//attacker.example/collect?stealing=1",
    {
      host: "attacker.invalid",
      "x-forwarded-host": "attacker.invalid",
      "x-forwarded-proto": "http",
    },
  );
  assert.equal(response.status, 308);
  const location = new URL(response.headers.get("location") ?? "");
  assert.equal(
    location.origin,
    CANONICAL_ORIGIN,
    `open redirect: got ${location.origin} instead of ${CANONICAL_ORIGIN}`,
  );
  assert.notEqual(location.origin, "https://attacker.example");
});

test("build-time origin is baked into the compiled Worker; runtime env has no effect", async () => {
  // If Vite `define` correctly injects the validated literal into the
  // bundle, mutating process.env AFTER build (and after this test file
  // loaded) must not change the Worker's canonical-origin behavior.
  const previous = process.env.PORTAL_CANONICAL_ORIGIN;
  process.env.PORTAL_CANONICAL_ORIGIN =
    "https://runtime-should-be-ignored.example";
  try {
    const response = await requestPortal(
      "https://preview.invalid/build-time-probe",
    );
    assert.equal(response.status, 308);
    const location = new URL(response.headers.get("location") ?? "");
    assert.equal(
      location.origin,
      CANONICAL_ORIGIN,
      `Worker used runtime env (${location.origin}) instead of build-time literal (${CANONICAL_ORIGIN})`,
    );
    assert.notEqual(
      location.origin,
      "https://runtime-should-be-ignored.example",
    );
  } finally {
    if (previous === undefined) {
      delete process.env.PORTAL_CANONICAL_ORIGIN;
    } else {
      process.env.PORTAL_CANONICAL_ORIGIN = previous;
    }
  }
});

test("removes the disposable starter and labels non-live evidence", async () => {
  const [htmlResponse, page, layout, registry, packageJson] = await Promise.all([
    render(),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/portal-registry.ts", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  const html = await htmlResponse.text();

  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
  assert.doesNotMatch(page, /SkeletonPreview|codex-preview/);
  assert.doesNotMatch(layout, /Starter Project|codex-preview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
  assert.match(page, /evidence mode/);
  assert.match(page, /not yet a live control plane/i);
  assert.match(registry, /state: "connect"/);
  assert.match(registry, /state: "planned"/);

  await assert.rejects(
    access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)),
  );
  await assert.rejects(
    access(new URL("../app/_sites-preview/preview.css", import.meta.url)),
  );
});

test("keeps private specialist endpoints out of the static registry", async () => {
  const registry = await readFile(
    new URL("../lib/portal-registry.ts", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(registry, /amazonaws\.com|grafana\.(?:internal|local)|127\.0\.0\.1/);
  assert.doesNotMatch(registry, /\b\d{12}\b/);
  assert.doesNotMatch(registry, /validatorPublicKey|secretRef|secretKeyRef|keystore/i);
  assert.match(registry, /eth-fleet-overview/);
  assert.match(registry, /eth-validator-detail/);
  assert.match(registry, /eth-validator-geth-lighthouse/);
  assert.match(registry, /eth-signer-slashing/);
  assert.match(registry, /eth-platform-logs/);
  assert.match(registry, /eth-platform-local-smoke/);
  assert.match(registry, /\/security\/dependabot/);
  assert.match(registry, /blob\/main\/\.github\/dependabot\.yml/);
  assert.match(registry, /\/issues\/43/);
  assert.match(registry, /GitHub admin API · verified 2026-08-02/);
  assert.match(registry, /ECR basic scan-on-push/);
  assert.doesNotMatch(registry, /\b(?:zero|0) (?:open )?(?:alerts|vulnerabilities|findings)\b/i);
});

test("Worker imports the validated canonical origin (not a duplicate constant)", async () => {
  const worker = await readFile(
    new URL("../worker/index.ts", import.meta.url),
    "utf8",
  );

  // Worker must import from the shared module.
  assert.match(worker, /from\s+["']\.\.\/lib\/canonical-origin["']/);
  // Worker must compare full origins, not just hostname+protocol.
  assert.match(worker, /requestUrl\.origin\s*!==\s*CANONICAL_ORIGIN/);
  // Worker must not carry its own hardcoded origin literal.
  assert.doesNotMatch(worker, /"https:\/\/[a-z0-9.-]+"/i);
});

test("labels operational evidence and removes fabricated progress magnitudes", async () => {
  const [htmlResponse, page, vite] = await Promise.all([
    render(),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../vite.config.ts", import.meta.url), "utf8"),
  ]);
  const html = await htmlResponse.text();

  assert.doesNotMatch(page, /amount:\s*"\d+%"/);
  assert.doesNotMatch(html, /\b(?:34|58|72|100)%\b/);
  assert.match(page, /Web3Signer key bundle · reviewed 2026-08-02/i);
  assert.match(page, /Terraform \+ EKS · observed 2026-08-02/i);
  assert.match(page, /source · operator handoff · 2026-08-02/i);
  assert.match(page, /tabIndex=\{0\}/);
  assert.doesNotMatch(vite, /site-creator-d1|site-creator-r2|PLACEHOLDER_DATABASE/);
});
