"use client";

import { apiBase, apiFetch as fetch } from "@/lib/api";

import { useEffect, useState } from "react";
import { Cpu, Mail, ShieldCheck, SlidersHorizontal } from "lucide-react";
import SettingsForm from "@/components/SettingsForm";

type Health = {
  status: string;
  detection_model: string;
  pose_model: string;
  specialized_model?: string | null;
  specialized_loaded: boolean;
  model_error?: string | null;
  camera_count: number;
};



export default function SettingsPage() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${apiBase}/health`, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Health endpoint failed (${response.status})`);
        setHealth(await response.json());
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Unable to reach backend"));
  }, []);

  return (
    <div className="max-w-5xl mx-auto pb-10">
      <header className="mb-8">
        <h2 className="text-3xl font-bold tracking-tight mb-2">Settings</h2>
        <p className="text-foreground/60">Runtime configuration status for the current ShopAware bootstrap.</p>
      </header>

      {error && <div className="glass-panel border-red-500/25 text-red-200 p-4 mb-5">{error}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <section className="glass-panel p-5">
          <div className="flex items-center gap-2 mb-4"><Cpu className="w-5 h-5 text-brand" /><h3 className="font-semibold">Inference models</h3></div>
          <dl className="space-y-3 text-sm">
            <div><dt className="text-foreground/45">Detection</dt><dd className="font-mono break-all">{health?.detection_model || "—"}</dd></div>
            <div><dt className="text-foreground/45">Pose</dt><dd className="font-mono break-all">{health?.pose_model || "—"}</dd></div>
            <div><dt className="text-foreground/45">Specialized model</dt><dd className="font-mono break-all">{health?.specialized_model || "not configured"}</dd></div>
            <div><dt className="text-foreground/45">Specialized loaded</dt><dd>{health?.specialized_loaded ? "yes" : "no"}</dd></div>
          </dl>
          <p className="mt-4 text-xs leading-5 text-foreground/45">
            Model paths are environment-driven so ShopAware can benchmark YOLO26n/s/m or use a custom retail/shoplifting checkpoint without rewriting the incident pipeline.
          </p>
        </section>

        <section className="glass-panel p-5">
          <div className="flex items-center gap-2 mb-4"><Mail className="w-5 h-5 text-brand" /><h3 className="font-semibold">Email alerts</h3></div>
          <p className="text-sm text-foreground/65 leading-6">
            SMTP is configured through server environment variables for this bootstrap. Secrets are intentionally not returned to the browser.
          </p>
          <div className="mt-4 font-mono text-xs text-foreground/50 leading-6">
            SMTP_HOST<br />SMTP_PORT<br />SMTP_USERNAME<br />SMTP_PASSWORD<br />SMTP_FROM<br />SMTP_TO
          </div>
        </section>

        <section className="glass-panel p-5">
          <div className="flex items-center gap-2 mb-4"><ShieldCheck className="w-5 h-5 text-brand" /><h3 className="font-semibold">Credential security</h3></div>
          <p className="text-sm text-foreground/65 leading-6">
            RTSP passwords are encrypted with a local Fernet key or an operator-supplied key. The normal camera API returns only masked stream sources and never returns decrypted passwords.
          </p>
        </section>

        <section className="glass-panel p-5">
          <div className="flex items-center gap-2 mb-4"><SlidersHorizontal className="w-5 h-5 text-brand" /><h3 className="font-semibold">Pending controls</h3></div>
          <ul className="text-sm text-foreground/65 space-y-2 list-disc pl-5">
            <li>per-camera overrides for global inference settings</li>
            <li>additional operator roles beyond administrator</li>
            <li>SMS alert provider</li>
          </ul>
        </section>
      </div>

      <SettingsForm />

      {health?.model_error && (
        <div className="glass-panel border-amber-500/25 text-amber-200 p-4 mt-5 text-sm">
          Model status: {health.model_error}
        </div>
      )}
    </div>
  );
}
