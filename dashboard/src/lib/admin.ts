import { apiBase, apiFetch } from "./api";

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(`${apiBase}${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status})`);
  return data as T;
}

export type CustomerGroup = { id: string; name: string; camera_count: number };
