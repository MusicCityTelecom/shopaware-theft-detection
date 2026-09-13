"use client";
import { useEffect, useState } from "react";
import { apiBase, apiFetch } from "@/lib/api";

type Zone = { id: string; name: string; type: string; points: [number, number][]; enabled: boolean };
const types = ["merchandise", "restricted", "checkout", "exit", "ignore"];

export default function ZoneEditor({ cameraId }: { cameraId: string }) {
  const [zones, setZones] = useState<Zone[]>([]);
  const [draft, setDraft] = useState<Zone>({ id: "", name: "New zone", type: "merchandise", points: [], enabled: true });
  const [error, setError] = useState("");
  const [drag, setDrag] = useState<number | null>(null);
  useEffect(() => { apiFetch(`${apiBase}/cameras/${cameraId}/zones`).then(async r => {
    if (!r.ok) throw new Error("Unable to load zones"); setZones(await r.json());
  }).catch(e => setError(e.message)); }, [cameraId]);

  async function persist(next: Zone[]) {
    try {
      const r = await apiFetch(`${apiBase}/cameras/${cameraId}/zones`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ zones: next }) });
      if (!r.ok) throw new Error("Unable to save. Use a non-intersecting polygon with at least three distinct points.");
      setZones(await r.json()); setError("");
    } catch (e) { setError(e instanceof Error ? e.message : "Zone save failed"); }
  }
  function position(e: React.PointerEvent<SVGSVGElement>): [number, number] {
    const rect = e.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (e.clientY - rect.top) / rect.height))];
  }
  return <section className="mt-4 space-y-3">
    <p className="text-sm">Click to add corners; drag a corner to edit. Zones use normalized coordinates.</p>
    <div className="relative aspect-video bg-black">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`${apiBase}/cameras/${cameraId}/frame`} alt="Camera frame for zone configuration; enable camera if unavailable" className="w-full h-full" />
      <svg className="absolute inset-0 w-full h-full touch-none" viewBox="0 0 1000 1000" preserveAspectRatio="none"
        onPointerDown={e => { if (e.target === e.currentTarget) setDraft({ ...draft, points: [...draft.points, position(e)] }); }}
        onPointerMove={e => { if (drag !== null) { const points = [...draft.points]; points[drag] = position(e); setDraft({ ...draft, points }); } }}
        onPointerUp={() => setDrag(null)} onPointerLeave={() => setDrag(null)}>
        {zones.filter(z => z.id !== draft.id && z.enabled).map(z => <polygon key={z.id} points={z.points.map(([x,y]) => `${x*1000},${y*1000}`).join(" ")} fill="#22c55e22" stroke="#22c55e" strokeWidth="3" pointerEvents="none" />)}
        <polygon points={draft.points.map(([x,y]) => `${x*1000},${y*1000}`).join(" ")} fill="#fbbf2433" stroke="#fbbf24" strokeWidth="4" pointerEvents="none" />
        {draft.points.map(([x,y],i) => <circle key={i} cx={x*1000} cy={y*1000} r="10" fill="#fbbf24" onPointerDown={e => { e.stopPropagation(); setDrag(i); }} />)}
      </svg>
    </div>
    <div className="flex flex-wrap gap-2">
      <input aria-label="Zone name" className="input w-40" value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} />
      <select aria-label="Zone type" className="input w-auto" value={draft.type} onChange={e => setDraft({ ...draft, type: e.target.value })}>{types.map(t => <option key={t}>{t}</option>)}</select>
      <button className="btn btn-secondary" onClick={() => setDraft({ ...draft, points: draft.points.slice(0,-1) })}>Undo corner</button>
      <button className="btn btn-primary" disabled={draft.points.length < 3} onClick={() => { const saved = { ...draft, id: draft.id || crypto.randomUUID() }; setDraft(saved); void persist([...zones.filter(z => z.id !== saved.id), saved]); }}>Save zone</button>
      <button className="btn btn-secondary" onClick={() => setDraft({ id: "", name: "New zone", type: "merchandise", points: [], enabled: true })}>New zone</button>
    </div>
    {zones.map(z => <div key={z.id} className="flex flex-wrap gap-2 text-sm items-center"><span>{z.name} · {z.type}</span><button className="btn btn-secondary" onClick={() => setDraft(z)}>Edit</button><button className="btn btn-secondary" onClick={() => persist(zones.map(item => item.id === z.id ? { ...item, enabled: !item.enabled } : item))}>{z.enabled ? "Disable" : "Enable"}</button><button className="btn btn-danger" onClick={() => persist(zones.filter(item => item.id !== z.id))}>Delete zone</button></div>)}
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </section>;
}
