/** Read-only Cloudflare Worker entry point for the operator portal. */
import handler from "vinext/server/app-router-entry";
import { CANONICAL_ORIGIN } from "../lib/canonical-origin";

const worker = {
  async fetch(request: Request, env: unknown, ctx: unknown): Promise<Response> {
    const requestUrl = new URL(request.url);

    // Compare the full request origin (scheme + host + port) to the
    // validated canonical origin. `url.origin` normalization means
    // ports and case are handled consistently on both sides.
    if (requestUrl.origin !== CANONICAL_ORIGIN) {
      // Construct the destination from CANONICAL_ORIGIN and assign
      // pathname + search directly. Using
      // `new URL(pathname + search, CANONICAL_ORIGIN)` would treat a
      // `//attacker.example/collect` pathname as a network-path
      // reference and produce an open redirect off-origin.
      const destination = new URL(CANONICAL_ORIGIN);
      destination.pathname = requestUrl.pathname;
      destination.search = requestUrl.search;
      return Response.redirect(destination, 308);
    }

    const response = await handler.fetch(request, env, ctx);
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set(
      "Strict-Transport-Security",
      "max-age=31536000; includeSubDomains",
    );
    responseHeaders.set("Referrer-Policy", "strict-origin-when-cross-origin");
    responseHeaders.set("X-Content-Type-Options", "nosniff");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  },
};

export default worker;
