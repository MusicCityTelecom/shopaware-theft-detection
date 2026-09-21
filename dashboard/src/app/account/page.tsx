"use client";
import { FormEvent, useState } from "react";
import { useSession } from "@/components/AuthGate";
import { apiJson } from "@/lib/admin";

export default function AccountPage() {
  const user = useSession();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    if (data.get("new_password") !== data.get("confirm_password")) { setError("New passwords do not match."); return; }
    setBusy(true); setError("");
    try {
      await apiJson("/auth/password", { method: "POST", body: JSON.stringify({ current_password: data.get("current_password"), new_password: data.get("new_password") }) });
      form.reset(); setDone(true);
    } catch (e) { setError(e instanceof Error ? e.message : "Password change failed"); }
    finally { setBusy(false); }
  }
  return <div className="max-w-xl mx-auto space-y-6">
    <header><h2 className="text-3xl font-bold mb-2">My account</h2><p>{user?.username} · {user?.role === "admin" ? "Administrator" : "User"}</p></header>
    {done ? <div className="glass-panel p-6"><p role="status">Password changed. All previous sessions have been signed out.</p><button className="btn btn-primary mt-4" onClick={() => window.location.assign("/")}>Sign in with your new password</button></div> :
      <form onSubmit={submit} className="glass-panel p-6 space-y-4">
        <h3 className="text-xl font-semibold">Change password</h3>
        <label className="block">Current password<input className="input" name="current_password" type="password" autoComplete="current-password" required maxLength={1024} /></label>
        <label className="block">New password<input className="input" name="new_password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} /></label>
        <label className="block">Confirm new password<input className="input" name="confirm_password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} /></label>
        <p className="text-sm text-foreground/60">Use at least 12 characters. Changing your password signs out all your sessions.</p>
        {error && <p role="alert" className="text-red-300">{error}</p>}
        <button disabled={busy} className="btn btn-primary">{busy ? "Changing…" : "Change password"}</button>
      </form>}
  </div>;
}
