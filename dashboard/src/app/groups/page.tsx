"use client";
import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { apiJson, CustomerGroup } from "@/lib/admin";

export default function GroupsPage() {
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  const [name, setName] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(async () => { setGroups(await apiJson<CustomerGroup[]>("/groups")); }, []);
  useEffect(() => { const timer = setTimeout(() => { refresh().catch(e => setError(e.message)); }, 0); return () => clearTimeout(timer); }, [refresh]);
  async function change(path: string, method: string, value?: string) {
    setBusy(true); setError("");
    try { await apiJson(path, { method, ...(value !== undefined ? { body: JSON.stringify({ name: value }) } : {}) }); await refresh(); setName(""); }
    catch (e) { setError(e instanceof Error ? e.message : "Group update failed"); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) { event.preventDefault(); change("/groups", "POST", name); }
  return <div className="max-w-4xl mx-auto space-y-6">
    <header><h2 className="text-3xl font-bold mb-2">Customer groups</h2><p className="text-foreground/60">Create one group per customer, place cameras in it, then grant users access from Users.</p></header>
    {error && <p role="alert" className="text-red-300">{error}</p>}
    <form onSubmit={submit} className="glass-panel p-5 flex gap-3 items-end"><label className="block flex-1">Customer group name<input required maxLength={128} className="input" value={name} onChange={e => setName(e.target.value)} /></label><button disabled={busy} className="btn btn-primary">Create group</button></form>
    {groups.length === 0 && <p>No customer groups yet.</p>}
    {groups.map(group => <section key={group.id} className="glass-panel p-5 space-y-3">
      <label className="block">Group name<input aria-label={`Group name for ${group.name}`} className="input" maxLength={128} value={names[group.id] ?? group.name} onChange={e => setNames({ ...names, [group.id]: e.target.value })} /></label>
      <p>{group.camera_count} cameras</p>
      <div className="flex gap-3 flex-wrap"><Link className="btn btn-secondary" href={`/cameras?group=${encodeURIComponent(group.id)}`}>View cameras</Link><button disabled={busy || !(names[group.id] ?? group.name).trim()} className="btn btn-secondary" onClick={() => change(`/groups/${group.id}`, "PUT", names[group.id] ?? group.name)}>Save name</button><button disabled={busy || group.camera_count > 0} className="btn btn-danger" onClick={() => { if (window.confirm(`Delete empty group ${group.name}? Its group access grants will be removed.`)) change(`/groups/${group.id}`, "DELETE"); }}>Delete empty group</button></div>
    </section>)}
  </div>;
}
