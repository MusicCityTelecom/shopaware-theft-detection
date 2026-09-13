"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, RefreshCw, ShieldAlert, XCircle } from "lucide-react";

type Incident = {
  id: string;
  camera_id: string;
  camera_name: string;
  event_type: string;
  message: string;
  risk_score: number;
  created_at: string;
  snapshot_path?: string | null;
  clip_path?: string | null;
  review_status: string;
  metadata?: Record<string, unknown>;
};

const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

function basename(path?: string | null) {
  if (!path) return null;
  return path.split(/[\\/]/).pop() || null;
}

function snapshotUrl(path?: string | null) {
  const name = basename(path);
  return name ? `${apiBase}/alerts/${encodeURIComponent(name)}` : null;
}

function clipUrl(path?: string | null) {
  const name = basename(path);
  return name ? `${apiBase}/incident-media/${encodeURIComponent(name)}` : null;
}

export default function HistoryPage() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [filter, setFilter] = useState("needs_review");
  const [error, setError] = useState("");

  const refresh = async () => {
    try {
      const response = await fetch(`${apiBase}/history?limit=300`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Incident history failed (${response.status})`);
      setIncidents(await response.json());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load incident history");
    }
  };

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 10000);
    return () => clearInterval(timer);
  }, []);

  const visible = useMemo(
    () => incidents.filter((incident) => filter === "all" || incident.review_status === filter),
    [incidents, filter],
  );

  const review = async (id: string, status: string) => {
    const response = await fetch(`${apiBase}/incidents/${id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (!response.ok) {
      setError(`Unable to update incident ${id.slice(0, 8)}`);
      return;
    }
    await refresh();
  };

  return (
    <div className="max-w-7xl mx-auto pb-10">
      <header className="mb-8 flex flex-wrap justify-between gap-4 items-end">
        <div>
          <h2 className="text-3xl font-bold tracking-tight mb-2">Incidents</h2>
          <p className="text-foreground/60">Review AI-generated candidates, snapshots, and pre/post-event evidence clips.</p>
        </div>
        <div className="flex gap-2">
          <select className="input w-auto" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="needs_review">Needs review</option>
            <option value="confirmed">Confirmed</option>
            <option value="false_alarm">False alarms</option>
            <option value="dismissed">Dismissed</option>
            <option value="all">All</option>
          </select>
          <button className="btn btn-secondary flex gap-2 items-center" onClick={refresh}><RefreshCw className="w-4 h-4" />Refresh</button>
        </div>
      </header>

      {error && <div className="glass-panel border-red-500/25 text-red-200 p-3 mb-4 text-sm">{error}</div>}

      {visible.length === 0 ? (
        <div className="glass-panel p-12 text-center text-foreground/50">
          <ShieldAlert className="w-10 h-10 mx-auto mb-3" />
          No incidents match this filter.
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {visible.map((incident) => {
            const image = snapshotUrl(incident.snapshot_path);
            const video = clipUrl(incident.clip_path);
            return (
              <article key={incident.id} className="glass-panel overflow-hidden">
                {video ? (
                  <div className="bg-black aspect-video">
                    <video
                      src={video}
                      controls
                      preload="metadata"
                      className="w-full h-full object-contain"
                    >
                      Your browser does not support embedded incident video.
                    </video>
                  </div>
                ) : image ? (
                  <div className="bg-black aspect-video">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={image} alt={`Incident from ${incident.camera_name}`} className="w-full h-full object-contain" />
                  </div>
                ) : null}

                <div className="p-4">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <div>
                      <div className="font-semibold">{incident.camera_name}</div>
                      <div className="text-xs text-foreground/45">{new Date(incident.created_at).toLocaleString()}</div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="badge text-amber-300">{Math.round(incident.risk_score * 100)}% risk</span>
                      <span className="badge">{incident.review_status.replaceAll("_", " ")}</span>
                    </div>
                  </div>

                  <div className="text-sm font-medium text-brand mb-1">{incident.event_type.replaceAll("_", " ")}</div>
                  <p className="text-sm text-foreground/65 leading-6 mb-4">{incident.message}</p>

                  {incident.clip_path ? (
                    <div className="text-xs text-green-300 mb-3">Pre/post-event evidence clip ready.</div>
                  ) : (
                    <div className="text-xs text-amber-300/80 mb-3">Evidence clip is still recording/finalizing, or clip creation failed.</div>
                  )}

                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => review(incident.id, "confirmed")} className="btn btn-secondary flex items-center gap-2 text-green-300"><CheckCircle2 className="w-4 h-4" />Confirm</button>
                    <button onClick={() => review(incident.id, "false_alarm")} className="btn btn-secondary flex items-center gap-2 text-amber-300"><XCircle className="w-4 h-4" />False alarm</button>
                    <button onClick={() => review(incident.id, "dismissed")} className="btn btn-secondary">Dismiss</button>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
