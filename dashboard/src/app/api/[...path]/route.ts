import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const base = process.env.SHOPAWARE_BACKEND_URL || "http://127.0.0.1:8000";
  const url = `${base.replace(/\/$/, "")}/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers(request.headers);
  for (const name of ["host", "connection", "content-length"]) headers.delete(name);
  const upstream = await fetch(url, { method: request.method, headers, redirect: "manual", cache: "no-store",
    body: ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer() });
  const responseHeaders = new Headers(upstream.headers);
  for (const name of ["content-encoding", "content-length", "connection"]) responseHeaders.delete(name);
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as GET, proxy as HEAD, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE, proxy as OPTIONS };
