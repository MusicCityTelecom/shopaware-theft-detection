"use client";

import { FormEvent, useEffect, useState } from "react";
import { apiBase, apiFetch, setCsrf } from "@/lib/api";

export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const reset = () => { setUser(null); setCsrf(""); };
    window.addEventListener("shopaware-auth-required", reset);
    apiFetch(`${apiBase}/auth/session`).then(async response => {
      if (response.ok) { const session = await response.json(); setCsrf(session.csrf); setUser(session.username); }
    }).catch(() => setError("Unable to reach ShopAware.")).finally(() => setLoading(false));
    return () => window.removeEventListener("shopaware-auth-required", reset);
  }, []);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/auth/login`, { method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: fields.get("username"), password: fields.get("password") }) });
      if (!response.ok) throw new Error(response.status === 429 ? "Too many attempts. Try again later." : "Login failed.");
      const session = await response.json();
      setCsrf(session.csrf); setUser(session.username); form.reset();
    } catch (e) { setError(e instanceof Error ? e.message : "Login failed."); }
  }

  async function logout() {
    try {
      const response = await apiFetch(`${apiBase}/auth/logout`, { method: "POST" });
      if (!response.ok) throw new Error("Logout failed.");
      setCsrf(""); setUser(null);
    } catch { setError("Unable to log out. Check the connection and retry."); }
  }

  if (loading) return <p className="p-8">Connecting to ShopAware…</p>;
  if (!user) return <div className="m-auto max-w-sm p-8">
    <h1 className="text-2xl font-bold mb-5">Sign in to ShopAware</h1>
    <form onSubmit={login} className="space-y-4">
      <label className="block">Username<input name="username" autoComplete="username" className="input" required /></label>
      <label className="block">Password<input name="password" type="password" autoComplete="current-password" className="input" required /></label>
      <button className="btn btn-primary" type="submit">Sign in</button>
    </form>
    <p role="alert" className="text-red-300 mt-3">{error}</p>
    <p className="text-sm mt-5 text-foreground/55">Use the administrator account created during appliance setup.</p>
  </div>;
  return <><div className="fixed right-4 bottom-3 z-50 glass-panel p-2 text-xs">{user} <button className="btn btn-secondary ml-2" onClick={logout}>Log out</button>{error && <p role="alert">{error}</p>}</div>{children}</>;
}
