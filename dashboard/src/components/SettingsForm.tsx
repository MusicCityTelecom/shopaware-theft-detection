"use client";
import { FormEvent, useEffect, useState } from "react";
import { apiBase, apiFetch } from "@/lib/api";

const fields: [string, string, string][] = [
  ["detection_model", "Detection model", "text"], ["pose_model", "Pose model", "text"],
  ["risk_threshold", "Heuristic risk threshold (0–100)", "number"], ["inference_fps", "Inference FPS", "number"],
  ["pre_seconds", "Pre-event seconds", "number"], ["post_seconds", "Post-event seconds", "number"],
  ["recording_fps", "Evidence FPS", "number"], ["retention_days", "Retention days", "number"],
  ["max_storage_bytes", "Media quota in bytes", "number"], ["smtp_host", "SMTP host", "text"],
  ["smtp_port", "SMTP port", "number"], ["smtp_username", "SMTP username", "text"],
  ["smtp_from", "Alert sender", "email"], ["smtp_to", "Alert recipient", "email"],
];

export default function SettingsForm() {
  const [settings, setSettings] = useState<Record<string, string | number | boolean> | null>(null);
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { apiFetch(`${apiBase}/settings`).then(async r => {
    if (!r.ok) throw new Error("Settings unavailable"); setSettings(await r.json());
  }).catch(e => setMessage(e.message)); }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      const response = await apiFetch(`${apiBase}/settings`, { method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...settings, ...(password ? { smtp_password: password } : {}) }) });
      if (!response.ok) throw new Error("Settings could not be saved. Check field values.");
      const result = await response.json(); setSettings(result.settings); setPassword("");
      setMessage("Saved. Restart the ShopAware backend to apply these settings.");
    } catch (e) { setMessage(e instanceof Error ? e.message : "Save failed"); }
  }
  return <section className="glass-panel p-5 mt-5"><h3 className="font-semibold mb-4">Configuration</h3>
    {settings && <form onSubmit={save} className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {fields.map(([key,label,type]) => <label key={key} className="text-sm">{label}<input className="input mt-1" type={type} step={type === "number" ? "any" : undefined} value={String(settings[key] ?? "")} onChange={e => setSettings({ ...settings, [key]: type === "number" ? Number(e.target.value) : e.target.value })} /></label>)}
      <label className="text-sm">Replacement SMTP password<input type="password" autoComplete="new-password" className="input mt-1" value={password} onChange={e => setPassword(e.target.value)} /><span className="text-xs">{settings.has_smtp_password ? "Password stored. Leave blank to keep it." : "No password stored."}</span></label>
      <button className="btn btn-primary self-end" type="submit">Save configuration</button>
    </form>}
    {message && <p role="status" className="mt-4 text-sm">{message}</p>}
  </section>;
}
