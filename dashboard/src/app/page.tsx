"use client";

import { apiBase, apiFetch as fetch } from "@/lib/api";

import { useEffect, useState } from "react";
import { Activity, Camera, Cpu, ShieldAlert } from "lucide-react";
import CameraGrid from "@/components/CameraGrid";

type CameraRow = { id: string; status: string };
type Incident = { id: string; created_at: string; review_status: string };
type Health = {
  status: string;
  detection_model: string;
  pose_model: string;
  specialized_loaded: boolean;
  camera_count: number;
  incident_counts?: Record<string, number>;
  telemetry?: { system_cpu_percent: number; process_rss_bytes: number; cuda_available: boolean };
  storage?: { bytes_used?: number; max_bytes?: number; quota_exceeded?: boolean };
};



export default function Home() {
  const [cameraCount, setCameraCount] = useState("0/0");
  const [openIncidents, setOpenIncidents] = useState(0);
  const [health, setHealth] = useState<Health | null>(null);
  const [apiError, setApiError] = useState("");

  useEffect(() => {
    const refresh = async () => {
      try {
        const [cameraRes, historyRes, healthRes] = await Promise.all([
          fetch(`${apiBase}/cameras`, { cache: "no-store" }),
          fetch(`${apiBase}/history?limit=200`, { cache: "no-store" }),
          fetch(`${apiBase}/health`, { cache: "no-store" }),
        ]);

        if (!cameraRes.ok || !historyRes.ok || !healthRes.ok) throw new Error("ShopAware API returned an error");
        const cameras: CameraRow[] = await cameraRes.json();
        const incidents: Incident[] = await historyRes.json();
        const h: Health = await healthRes.json();
        const active = cameras.filter((c) => c.status === "active").length;
        setCameraCount(`${active}/${cameras.length}`);
        setOpenIncidents(h.incident_counts?.needs_review ?? incidents.filter((i) => i.review_status === "needs_review").length);
        setHealth(h);
        setApiError("");
      } catch (error) {
        setApiError(error instanceof Error ? error.message : "Unable to reach ShopAware API");
      }
    };

    refresh();
    const interval = setInterval(refresh, 10000);
    return () => clearInterval(interval);
  }, []);

  const cards = [
    { label: "Active Cameras", value: cameraCount, icon: Camera, detail: "healthy / configured" },
    { label: "Needs Review", value: String(openIncidents), icon: ShieldAlert, detail: "incident candidates" },
    { label: "Detector", value: health?.detection_model || "—", icon: Cpu, detail: health?.specialized_loaded ? "specialized model active" : "generic detector" },
    { label: "Backend", value: health?.status || "offline", icon: Activity, detail: health?.pose_model || "pose model unavailable" },
    { label: "CPU", value: health?.telemetry ? `${health.telemetry.system_cpu_percent.toFixed(1)}%` : "—", icon: Cpu, detail: "sampled system CPU" },
    { label: "Memory", value: health?.telemetry ? `${Math.round(health.telemetry.process_rss_bytes / 1024 ** 2)} MiB` : "—", icon: Activity, detail: "backend resident memory" },
    { label: "Media", value: health?.storage?.bytes_used !== undefined ? `${(health.storage.bytes_used / 1024 ** 3).toFixed(2)} GiB` : "—", icon: ShieldAlert, detail: health?.storage?.quota_exceeded ? "quota exceeded" : "retention-managed storage" },
    { label: "CUDA", value: health?.telemetry ? (health.telemetry.cuda_available ? "Available" : "Unavailable") : "—", icon: Cpu, detail: "runtime availability; not qualification" },
  ];

  return (
    <div className="max-w-7xl mx-auto pb-10">
      <header className="mb-8 flex flex-wrap gap-4 justify-between items-end">
        <div>
          <h2 className="text-3xl font-bold tracking-tight mb-2">ShopAware Overview</h2>
          <p className="text-foreground/60">Live retail monitoring and incident review.</p>
        </div>
        <div className="text-xs text-foreground/45 max-w-md text-right">
          Candidate detections require human review. ShopAware does not treat a model alert as proof of theft.
        </div>
      </header>

      {apiError && (
        <div className="glass-panel border-amber-500/30 text-amber-200 p-4 mb-6">
          API status: {apiError}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 mb-8">
        {cards.map(({ label, value, icon: Icon, detail }) => (
          <div key={label} className="glass-panel p-4 flex gap-4 items-center min-h-24">
            <div className="p-3 rounded-xl bg-black/20 text-brand"><Icon className="w-6 h-6" /></div>
            <div className="min-w-0">
              <div className="text-xl font-bold truncate">{value}</div>
              <div className="text-sm text-foreground/70">{label}</div>
              <div className="text-[11px] text-foreground/40 truncate">{detail}</div>
            </div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between mb-4">
        <h3 className="text-xl font-semibold">Live Feeds</h3>
        <div className="text-xs text-foreground/45">WebSocket preview; evidence clips will use the recorder pipeline.</div>
      </div>
      <CameraGrid />
    </div>
  );
}
