import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const bodyLimit = 1024 * 1024;
function cleanHeaders(input: Headers): Headers {
  const headers = new Headers(input);
  const connectionHeaders = (headers.get("connection") || "").split(",");
  for (const name of [...connectionHeaders, "host", "connection", "content-length", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"]) {
    if (name.trim()) headers.delete(name.trim());
  }
  return headers;
}

function error(status: number, detail: string) {
  return Response.json({ detail }, { status, headers: { "cache-control": "no-store" } });
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const base = process.env.SHOPAWARE_BACKEND_URL || "http://127.0.0.1:8000";
  const url = `${base.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  let body: Uint8Array<ArrayBuffer> | undefined;
  if (!["GET", "HEAD"].includes(request.method) && request.body) {
    if (Number(request.headers.get("content-length")) > bodyLimit) {
      await request.body.cancel();
      return error(413, "Request body exceeds 1 MiB");
    }
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let length = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.length;
        if (length > bodyLimit) {
          await reader.cancel();
          return error(413, "Request body exceeds 1 MiB");
        }
        chunks.push(value);
      }
    } catch {
      return error(400, "Request body could not be read");
    } finally {
      reader.releaseLock();
    }
    body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.length; }
  }
  try {
    const upstream = await fetch(url, { method: request.method, headers: cleanHeaders(request.headers),
      redirect: "manual", cache: "no-store", signal: request.signal, body });
    const responseHeaders = cleanHeaders(upstream.headers);
    // fetch decompresses upstream responses before exposing their body.
    responseHeaders.delete("content-encoding");
    responseHeaders.set("cache-control", "no-store");
    return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
  } catch {
    return error(502, "ShopAware backend is unavailable");
  }
}

export { proxy as GET, proxy as HEAD, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as OPTIONS };
