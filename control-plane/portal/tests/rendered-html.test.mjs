import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

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

test("server-renders the environment status page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Ethereum Validator Platform<\/title>/i);
  assert.match(
    html,
    new RegExp(`property="og:image" content="${CANONICAL_ORIGIN_RE}/og\\.png"`, "i"),
  );
  assert.match(
    html,
    new RegExp(`rel="canonical" href="${CANONICAL_ORIGIN_RE}/?"`, "i"),
  );
  assert.match(html, /name="twitter:card" content="summary_large_image"/i);
  assert.match(html, /Environment status/);
  assert.match(html, /Loading live status/);
  assert.match(html, /Kubernetes and node dashboards/);
  assert.match(html, /Signing validators<\/span><strong>Unavailable/);
  assert.match(
    html,
    /href="https:\/\/ops\.g\.j2d3\.com\/grafana\/d\/eth-eks-ephemery-sync\/ethereum-platform-eks-ephemery-sync-evidence\?orgId=1"/,
  );
  assert.doesNotMatch(html, /Signing<\/span><strong>Disabled/);
  assert.match(html, /Client-pair sync/);
  assert.match(html, /Repository security/);
  assert.match(html, /Container image scan/);
  assert.match(html, /Dependency updates/);
  assert.match(html, /Image enforcement/);
  assert.match(html, /Project links/);
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
  assert.match(html, new RegExp(`${CANONICAL_ORIGIN_RE}/og\\.png`, "i"));
  assert.doesNotMatch(html, /attacker\.invalid/i);
});

test("redirect stays on canonical origin for a network-path reference", async () => {
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
  assert.equal(location.origin, CANONICAL_ORIGIN);
  assert.notEqual(location.origin, "https://attacker.example");
});

test("build-time origin is baked into the compiled Worker", async () => {
  const previous = process.env.PORTAL_CANONICAL_ORIGIN;
  process.env.PORTAL_CANONICAL_ORIGIN =
    "https://runtime-should-be-ignored.example";
  try {
    const response = await requestPortal(
      "https://preview.invalid/build-time-probe",
    );
    assert.equal(response.status, 308);
    const location = new URL(response.headers.get("location") ?? "");
    assert.equal(location.origin, CANONICAL_ORIGIN);
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

test("renders only functional navigation", async () => {
  const response = await render();
  const html = await response.text();
  const anchors = [...html.matchAll(/<a\b[^>]*>/gi)].map((match) => match[0]);

  assert.ok(anchors.length > 0);
  for (const anchor of anchors) {
    const href = anchor.match(/\bhref="([^"]+)"/i)?.[1];
    assert.ok(href, `anchor is missing href: ${anchor}`);

    if (href.startsWith("#")) {
      assert.match(html, new RegExp(`\\bid="${escapeForRegex(href.slice(1))}"`));
      continue;
    }

    const destination = new URL(href);
    assert.equal(destination.protocol, "https:");
    if (destination.hostname === "github.com") continue;
    assert.equal(destination.hostname, "ops.g.j2d3.com");
    assert.match(destination.pathname, /^\/grafana(?:\/|$)/);
  }

  assert.doesNotMatch(html, /<button\b|role="button"/i);
  assert.doesNotMatch(html, /aria-disabled="true"|href=""|href="#"/i);
});

test("source links point to tracked repository paths", async () => {
  const registry = await readFile(
    new URL("../lib/portal-registry.ts", import.meta.url),
    "utf8",
  );
  const linkedPaths = [
    ...registry.matchAll(/\$\{repository\}\/(?:blob|tree)\/main\/([^`]+)`/g),
  ].map((match) => match[1]);

  assert.ok(linkedPaths.length >= 7);
  for (const linkedPath of linkedPaths) {
    await access(new URL(`../../../${linkedPath}`, import.meta.url));
  }
});

test("excludes placeholders and marketing copy", async () => {
  const [htmlResponse, page, layout, registry, packageJson] = await Promise.all([
    render(),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/portal-registry.ts", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  const html = await htmlResponse.text();
  const portalSource = `${page}\n${layout}\n${registry}`;

  assert.doesNotMatch(
    `${html}\n${portalSource}`,
    /ethereum validator operations|hoodi \/ us-west-2|one view of the system|none of the comforting lies|spec-built|field console|evidence mode|fleet posture|ready is not authorized|front door|roadmap|explore the platform|planned adapter|coming soon|connect ↗/i,
  );
  assert.doesNotMatch(html, /codex-preview|Your site is taking shape|react-loading-skeleton/i);
  assert.doesNotMatch(page, /SkeletonPreview|codex-preview/);
  assert.doesNotMatch(layout, /Starter Project|codex-preview/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);

  await assert.rejects(
    access(new URL("../app/_sites-preview/SkeletonPreview.tsx", import.meta.url)),
  );
  await assert.rejects(
    access(new URL("../app/_sites-preview/preview.css", import.meta.url)),
  );
});

test("keeps private endpoints and identifiers out of portal source", async () => {
  const registry = await readFile(
    new URL("../lib/portal-registry.ts", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(registry, /amazonaws\.com|grafana\.(?:internal|local)|127\.0\.0\.1/);
  assert.doesNotMatch(registry, /\b\d{12}\b/);
  assert.doesNotMatch(
    registry,
    /validatorPublicKey|secretRef|secretKeyRef|keystore/i,
  );
});

test("live status uses the exact public adapter and polls without controls", async () => {
  const [component, registry] = await Promise.all([
    readFile(new URL("../components/live-status.tsx", import.meta.url), "utf8"),
    readFile(new URL("../lib/portal-registry.ts", import.meta.url), "utf8"),
  ]);

  assert.match(registry, /statusEndpoint\s*=\s*`\$\{operationsOrigin\}\/api\/status`/);
  assert.match(registry, /operationsOrigin\s*=\s*"https:\/\/ops\.g\.j2d3\.com"/);
  assert.match(component, /POLL_INTERVAL_MS\s*=\s*15_000/);
  assert.match(component, /fetch\(statusEndpoint/);
  assert.match(component, /cache:\s*"no-store"/);
  assert.match(component, /window\.setInterval\(load,\s*POLL_INTERVAL_MS\)/);
  assert.match(component, /Firing alerts/);
  assert.match(component, /Alert evaluation unavailable/);
  assert.match(component, /alertsAvailable/);
  assert.match(component, /href=\{alertsDashboard\}/);
  assert.match(registry, /alertsDashboard\s*=\s*`\$\{grafanaBase\}\/alerting\/list`/);
  assert.doesNotMatch(component, /<button\b|role="button"/i);
  assert.doesNotMatch(component, /customer|validatorPublicKey|secretRef|keystore/i);
});

test("Worker imports the shared canonical origin", async () => {
  const worker = await readFile(
    new URL("../worker/index.ts", import.meta.url),
    "utf8",
  );

  assert.match(worker, /from\s+["']\.\.\/lib\/canonical-origin["']/);
  assert.match(worker, /requestUrl\.origin\s*!==\s*CANONICAL_ORIGIN/);
  assert.doesNotMatch(worker, /"https:\/\/[a-z0-9.-]+"/i);
});
