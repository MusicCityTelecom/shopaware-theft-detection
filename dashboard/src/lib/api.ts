export const apiBase = process.env.NEXT_PUBLIC_API_URL || "/api";
let csrf = "";

export function setCsrf(value: string) { csrf = value; }

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (init.method && !["GET", "HEAD"].includes(init.method)) headers.set("X-CSRF-Token", csrf);
  const response = await globalThis.fetch(input, { ...init, headers, credentials: "include" });
  if (response.status === 401) window.dispatchEvent(new Event("shopaware-auth-required"));
  return response;
}
