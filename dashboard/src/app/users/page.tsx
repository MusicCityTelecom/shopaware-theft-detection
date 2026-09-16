"use client";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useSession } from "@/components/AuthGate";
import { apiJson, CustomerGroup } from "@/lib/admin";

type User = { id: number; username: string; role: "admin" | "user"; enabled: boolean; camera_ids: string[]; group_ids: string[] };
type Camera = { id: string; name: string; group_name: string | null };
const blank = { username: "", role: "user" as "admin" | "user", enabled: true, camera_ids: [] as string[], group_ids: [] as string[] };

export default function UsersPage() {
  const current = useSession();
  const [users, setUsers] = useState<User[]>([]);
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState(blank);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const refresh = useCallback(async () => {
    const [u, g, c] = await Promise.all([apiJson<User[]>("/users"), apiJson<CustomerGroup[]>("/groups"), apiJson<Camera[]>("/cameras")]);
    setUsers(u); setGroups(g); setCameras(c);
  }, []);
  useEffect(() => { const timer = setTimeout(() => { refresh().catch(e => setError(e.message)); }, 0); return () => clearTimeout(timer); }, [refresh]);
  function choose(user?: User) {
    setEditing(user?.id ?? null); setForm(user ? { username: user.username, role: user.role, enabled: user.enabled, camera_ids: user.camera_ids, group_ids: user.group_ids } : blank);
    setPassword(""); setError(""); setMessage("");
  }
  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try { await action(); setPassword(""); await refresh(); setMessage(success); }
    catch (e) { setError(e instanceof Error ? e.message : "Request failed"); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const result = await apiJson<{ id: number }>(editing === null ? "/users" : `/users/${editing}`, {
        method: editing === null ? "POST" : "PUT", body: JSON.stringify(editing === null ? { ...form, password } : form),
      });
      setEditing(result.id);
    }, "User saved. Changes revoke that user's active sessions.");
  }
  function toggle(field: "camera_ids" | "group_ids", id: string) {
    setForm({ ...form, [field]: form[field].includes(id) ? form[field].filter(v => v !== id) : [...form[field], id] });
  }
  return <div className="max-w-6xl mx-auto space-y-6">
    <header><h2 className="text-3xl font-bold mb-2">Users and camera access</h2><p className="text-foreground/60">Administrators see all customers and cameras. Users see and review only the cameras you assign.</p></header>
    {error && <p role="alert" className="text-red-300">{error}</p>}{message && <p role="status" className="text-green-300">{message}</p>}
    <div className="grid lg:grid-cols-[280px_1fr] gap-6">
      <section className="glass-panel p-4 space-y-3 h-fit"><button disabled={busy} className="btn btn-primary w-full" onClick={() => choose()}>Add user</button>
        {users.map(user => <button disabled={busy} key={user.id} onClick={() => choose(user)} className={`block text-left w-full p-3 rounded border ${editing === user.id ? "border-brand" : "border-glass-border"}`}><strong>{user.username}</strong><span className="block text-sm text-foreground/55">{user.role === "admin" ? "Administrator · all cameras" : "User"} · {user.enabled ? "Enabled" : "Disabled"}</span></button>)}
      </section>
      <form onSubmit={submit} className="glass-panel p-6 space-y-5">
        <h3 className="text-xl font-semibold">{editing === null ? "Create user" : `Edit ${form.username}`}</h3>
        <fieldset disabled={busy} className="space-y-4">
          <label className="block">Username<input className="input" required maxLength={128} value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} autoComplete="off" /></label>
          <label className="block">Role<select className="input" value={form.role} disabled={editing === current?.id} onChange={e => setForm({ ...form, role: e.target.value as "admin" | "user" })}><option value="user">User — assigned cameras</option><option value="admin">Administrator — all cameras and settings</option></select></label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={form.enabled} disabled={editing === current?.id} onChange={e => setForm({ ...form, enabled: e.target.checked })} />Account enabled</label>
          {form.role === "user" && <>
            <fieldset className="border border-glass-border rounded p-4 space-y-2"><legend>Customer group access</legend><p className="text-sm text-foreground/55">Includes every current and future camera in the selected groups.</p>
              {groups.length === 0 && <p>Create customer groups from Customers first, or assign individual cameras below.</p>}
              {groups.map(group => <label key={group.id} className="flex gap-2 items-center"><input type="checkbox" checked={form.group_ids.includes(group.id)} onChange={() => toggle("group_ids", group.id)} />{group.name} ({group.camera_count} cameras)</label>)}
            </fieldset>
            <fieldset className="border border-glass-border rounded p-4 space-y-2"><legend>Individual camera access</legend><p className="text-sm text-foreground/55">Adds specific cameras to the group access above. No selections means no camera access.</p>
              {cameras.length === 0 && <p>Add cameras from the Cameras page first.</p>}
              {cameras.map(camera => <label key={camera.id} className="flex gap-2 items-center"><input type="checkbox" checked={form.camera_ids.includes(camera.id)} onChange={() => toggle("camera_ids", camera.id)} />{camera.name} · {camera.group_name || "Ungrouped"}</label>)}
            </fieldset>
          </>}
          {editing === null && <label className="block">Initial password<input className="input" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} /><span className="text-sm text-foreground/55">At least 12 characters. Share it directly with the user; they can change it in My account.</span></label>}
          <button className="btn btn-primary" type="submit">{busy ? "Saving…" : "Save user"}</button>
        </fieldset>
        {editing !== null && editing !== current?.id && <fieldset disabled={busy} className="border-t border-glass-border pt-5 space-y-3">
          <label className="block">Reset password<input className="input" type="password" autoComplete="new-password" minLength={12} maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} /></label>
          <button type="button" disabled={password.length < 12} className="btn btn-secondary" onClick={() => run(() => apiJson(`/users/${editing}/password`, { method: "POST", body: JSON.stringify({ password }) }), "Password reset. Existing sessions were signed out.")}>Reset password</button>
          <button type="button" className="btn btn-danger ml-3" onClick={() => { if (window.confirm(`Delete user ${form.username}? Their access will be revoked.`)) run(async () => { await apiJson(`/users/${editing}`, { method: "DELETE" }); choose(); }, "User deleted."); }}>Delete user</button>
        </fieldset>}
        {editing === current?.id && <p className="text-sm">Change your own password from <a className="text-brand underline" href="/account">My account</a>.</p>}
      </form>
    </div>
  </div>;
}
