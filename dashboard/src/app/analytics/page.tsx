"use client";

import { apiBase, apiFetch as fetch } from "@/lib/api";
import { groupFaceTracks } from "@/lib/face-track-groups";
import { useEffect, useMemo, useState } from "react";
import { CarFront, RefreshCw, ScanFace, ScanSearch } from "lucide-react";

type Observation = {
  id: string;
  camera_id: string;
  camera_name: string;
  group_id: string | null;
  group_name: string | null;
  mode: "lpr" | "face_capture";
  observed_at: string;
  subject_key: string;
  label_text: string;
  confidence: number;
  snapshot_path?: string | null;
  metadata?: {
    track_scope?: string;
    quality?: number;
    vehicle_color?: string;
    vehicle_color_confidence?: number;
    vehicle_make?: string | null;
    vehicle_model?: string | null;
  };
};

export default function AnalyticsPage() {
  const [observations, setObservations] = useState<Observation[]>([]);
  const [mode, setMode] = useState("all");
  const [group, setGroup] = useState("");
  const [error, setError] = useState("");

  const refresh = async () => {
    try {
      const response = await fetch(`${apiBase}/observations?limit=500`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Analytics history failed (${response.status})`);
      setObservations(await response.json());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load analytics history");
    }
  };

  useEffect(() => {
    const initial = setTimeout(refresh, 0);
    const timer = setInterval(refresh, 15000);
    return () => { clearTimeout(initial); clearInterval(timer); };
  }, []);

  const visible = useMemo(
    () => observations.filter(item => (mode === "all" || item.mode === mode) && (!group || (item.group_id || "ungrouped") === group)),
    [observations, mode, group],
  );
  const faceGroups = useMemo(() => groupFaceTracks(visible), [visible]);
  const plates = visible.filter(item => item.mode === "lpr");

  return (
    <div className="max-w-7xl mx-auto pb-10">
      <header className="mb-8 flex flex-wrap justify-between gap-4 items-end">
        <div>
          <h2 className="text-3xl font-bold tracking-tight mb-2">Analytics</h2>
          <p className="text-foreground/60">Review license-plate candidates and anonymous face captures from cameras you can access.</p>
        </div>
        <button className="btn btn-secondary flex gap-2 items-center" onClick={refresh}><RefreshCw className="w-4 h-4" />Refresh</button>
      </header>

      <div className="grid sm:grid-cols-2 gap-4 mb-6 max-w-3xl">
        <label>Observation type<select className="input" value={mode} onChange={event => setMode(event.target.value)}><option value="all">All observations</option><option value="lpr">License plates</option><option value="face_capture">Face captures</option></select></label>
        <label>Customer<select className="input" value={group} onChange={event => setGroup(event.target.value)}><option value="">All accessible customers</option><option value="ungrouped">Ungrouped</option>{Array.from(new Map(observations.filter(item => item.group_id).map(item => [item.group_id, item.group_name])).entries()).map(([id, name]) => <option key={id} value={id!}>{name || "Former customer group"}</option>)}</select></label>
      </div>

      <div className="glass-panel p-4 mb-6 text-sm text-foreground/65 leading-6">
        OCR, color, and behavior results are confidence-scored candidates and require human verification. Face Capture does not identify people or match them across visits; it groups snapshots only while one camera track remains continuous.
      </div>
      {error && <div className="glass-panel border-red-500/25 text-red-200 p-3 mb-4 text-sm">{error}</div>}

      {(mode === "all" || mode === "lpr") && <section className="mb-8">
        <div className="flex gap-2 items-center mb-3"><CarFront className="w-5 h-5 text-brand" /><h3 className="text-xl font-semibold">License plates</h3><span className="badge">{plates.length}</span></div>
        {plates.length === 0 ? <Empty text="No plate candidates match this filter." /> : <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-4">{plates.map(item => <article key={item.id} className="glass-panel overflow-hidden">
          <Snapshot item={item} alt={`Plate candidate ${item.label_text}`} />
          <div className="p-4">
            <div className="flex items-center justify-between gap-2"><div className="font-mono text-xl font-bold tracking-wider">{item.label_text || "Unreadable"}</div><span className="badge">OCR {Math.round(item.confidence * 100)}%</span></div>
            <div className="text-sm mt-3">{item.camera_name} · {item.group_name || "Ungrouped"}</div>
            <div className="text-xs text-foreground/45">{new Date(item.observed_at).toLocaleString()}</div>
            <dl className="grid grid-cols-2 text-sm gap-y-1 mt-3"><dt className="text-foreground/50">Estimated color</dt><dd>{item.metadata?.vehicle_color || "unknown"}</dd><dt className="text-foreground/50">Make/model</dt><dd>{[item.metadata?.vehicle_make, item.metadata?.vehicle_model].filter(Boolean).join(" ") || "not classified"}</dd></dl>
          </div>
        </article>)}</div>}
      </section>}

      {(mode === "all" || mode === "face_capture") && <section>
        <div className="flex gap-2 items-center mb-3"><ScanFace className="w-5 h-5 text-brand" /><h3 className="text-xl font-semibold">Anonymous face tracks</h3><span className="badge">{faceGroups.length}</span></div>
        <p className="text-sm text-foreground/60 mb-3">Older captures are shown separately because their track continuity cannot be verified.</p>
        {faceGroups.length === 0 ? <Empty text="No face captures match this filter." /> : <div className="space-y-4">{faceGroups.map(items => <article key={items[0].id} className="glass-panel p-4">
          <div className="flex flex-wrap justify-between gap-2 mb-3"><div><div className="font-semibold">Anonymous camera track</div><div className="text-sm">{items[0].camera_name} · {items[0].group_name || "Ungrouped"}</div></div><div className="text-right"><span className="badge">{items.length} capture{items.length === 1 ? "" : "s"}</span><div className="text-xs text-foreground/45 mt-1">{new Date(items[0].observed_at).toLocaleString()}</div></div></div>
          <div className="flex gap-3 overflow-x-auto pb-1">{items.map(item => <div className="shrink-0 w-36" key={item.id}><Snapshot item={item} alt="Anonymous face capture" square /><div className="text-xs text-center mt-1 text-foreground/50">quality {Math.round((item.metadata?.quality || 0) * 100)}%</div></div>)}</div>
        </article>)}</div>}
      </section>}
    </div>
  );
}

function Snapshot({ item, alt, square = false }: { item: Observation; alt: string; square?: boolean }) {
  if (!item.snapshot_path) return <div className={`${square ? "aspect-square" : "aspect-video"} bg-black/30 flex items-center justify-center`}><ScanSearch className="w-7 h-7 text-foreground/30" /></div>;
  return <div className={`${square ? "aspect-square rounded-lg" : "aspect-video"} bg-black overflow-hidden`}>
    {/* eslint-disable-next-line @next/next/no-img-element */}
    <img src={`${apiBase}/observations/${item.id}/snapshot`} alt={alt} className="w-full h-full object-contain" />
  </div>;
}

function Empty({ text }: { text: string }) {
  return <div className="glass-panel p-10 text-center text-foreground/50"><ScanSearch className="w-9 h-9 mx-auto mb-3" />{text}</div>;
}
