import vinext from "vinext";
import { defineConfig } from "vite";
import { sites } from "./build/sites-vite-plugin.ts";
import {
  assertCanonicalOrigin,
  DEFAULT_CANONICAL_ORIGIN,
} from "./lib/canonical-origin-validator.mjs";

// Validate PORTAL_CANONICAL_ORIGIN at config-load time and capture the
// validated literal for `define`-based build-time injection below. An invalid
// value throws here, before any build work happens — a deployable artifact is
// never produced from a misconfigured environment.
const VALIDATED_CANONICAL_ORIGIN = assertCanonicalOrigin(
  process.env.PORTAL_CANONICAL_ORIGIN ?? DEFAULT_CANONICAL_ORIGIN,
);

// The restricted macOS sandbox reports itself through CODEX_SANDBOX and blocks
// FSEvents, so local previews use polling there. Other environments keep their
// native watcher.
const isCodexSeatbeltSandbox = process.env.CODEX_SANDBOX === "seatbelt";

const localBindingConfig = {
  main: "./worker/index.ts",
  compatibility_flags: ["nodejs_compat"],
};

export default defineConfig(async () => {
  // Keep Wrangler and Miniflare state project-local. These are non-secret tool
  // settings; application environment belongs in ignored `.env*` files.
  process.env.WRANGLER_WRITE_LOGS ??= "false";
  process.env.WRANGLER_LOG_PATH ??= ".wrangler/logs";
  process.env.MINIFLARE_REGISTRY_PATH ??= ".wrangler/registry";

  // Wrangler snapshots its log path while the Cloudflare plugin is imported.
  const { cloudflare } = await import("@cloudflare/vite-plugin");

  return {
    // Inject the validated canonical origin as a compile-time string
    // literal so the compiled Worker, RSC, and SSR bundles carry the
    // build-time value directly and cannot be influenced by
    // runtime env changes. The .ts wrapper's expression
    // `process.env.PORTAL_CANONICAL_ORIGIN ?? DEFAULT_CANONICAL_ORIGIN`
    // therefore evaluates to a string literal at runtime.
    define: {
      "process.env.PORTAL_CANONICAL_ORIGIN": JSON.stringify(
        VALIDATED_CANONICAL_ORIGIN,
      ),
    },
    server: isCodexSeatbeltSandbox
      ? { watch: { useFsEvents: false, usePolling: true } }
      : undefined,
    plugins: [
      vinext(),
      sites(),
      cloudflare({
        viteEnvironment: { name: "rsc", childEnvironments: ["ssr"] },
        config: localBindingConfig,
      }),
    ],
  };
});
