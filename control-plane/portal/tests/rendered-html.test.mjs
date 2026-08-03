import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

async function requestPortal(url = "https://g.j2d3.com/", headers = {}) {
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
    /property="og:image" content="https:\/\/g\.j2d3\.com\/og\.png"/i,
  );
  assert.match(html, /rel="canonical" href="https:\/\/g\.j2d3\.com\/?"/i);
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

test("serves content only on the canonical HTTPS hostname", async () => {
  for (const url of [
    "http://g.j2d3.com/path?mode=read",
    "https://preview.invalid/path?mode=read",
  ]) {
    const response = await requestPortal(url, {
      host: "attacker.invalid",
      "x-forwarded-host": "attacker.invalid",
      "x-forwarded-proto": "http",
    });
    assert.equal(response.status, 308);
    assert.equal(
      response.headers.get("location"),
      "https://g.j2d3.com/path?mode=read",
    );
  }

  const canonical = await requestPortal("https://g.j2d3.com/", {
    "x-forwarded-host": "attacker.invalid",
  });
  const html = await canonical.text();
  assert.equal(canonical.status, 200);
  assert.match(html, /https:\/\/g\.j2d3\.com\/og\.png/i);
  assert.doesNotMatch(html, /attacker\.invalid/i);
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

test("labels operational evidence and removes fabricated progress magnitudes", async () => {
  const [htmlResponse, page, worker, vite] = await Promise.all([
    render(),
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../worker/index.ts", import.meta.url), "utf8"),
    readFile(new URL("../vite.config.ts", import.meta.url), "utf8"),
  ]);
  const html = await htmlResponse.text();

  assert.doesNotMatch(page, /amount:\s*"\d+%"/);
  assert.doesNotMatch(html, /\b(?:34|58|72|100)%\b/);
  assert.match(page, /Web3Signer key bundle · reviewed 2026-08-02/i);
  assert.match(page, /Terraform \+ EKS · observed 2026-08-02/i);
  assert.match(page, /source · operator handoff · 2026-08-02/i);
  assert.match(page, /tabIndex=\{0\}/);
  assert.match(worker, /g\.j2d3\.com/);
  assert.doesNotMatch(worker, /_vinext\/image|D1Database|env\.IMAGES/);
  assert.doesNotMatch(vite, /site-creator-d1|site-creator-r2|PLACEHOLDER_DATABASE/);
});
