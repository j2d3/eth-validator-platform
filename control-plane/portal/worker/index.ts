/** Read-only Cloudflare Worker entry point for the operator portal. */
import handler from "vinext/server/app-router-entry";

const CANONICAL_ORIGIN = "https://g.j2d3.com";

const worker = {
  async fetch(request: Request, env: unknown, ctx: unknown): Promise<Response> {
    const requestUrl = new URL(request.url);

    if (
      requestUrl.protocol !== "https:" ||
      requestUrl.hostname !== "g.j2d3.com"
    ) {
      const destination = new URL(`${requestUrl.pathname}${requestUrl.search}`, CANONICAL_ORIGIN);
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
