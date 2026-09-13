"use client";

import { apiBase, apiFetch as fetch } from "@/lib/api";

import { FormEvent, useEffect, useState } from "react";
import { Camera, Plus, RefreshCw, Trash2 } from "lucide-react";
import ZoneEditor from "@/components/ZoneEditor";

type CameraRow = {
  id: string;
  name: string;
  rtsp_url: string;
  username: string;
  has_password: boolean;
  source: string;
  status: string;
  enabled: boolean;
  last_frame_at?: number;
  roi_points: number[][];
};



export default function CamerasPage() {
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [name, setName] = useState("");
  const [rtspUrl, setRtspUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [zoneCamera, setZoneCamera] = useState<string | null>(null);

  const cameraAction = async (camera: CameraRow, action: "enabled" | "test") => {
    try {
      const response = await fetch(`${apiBase}/cameras/${camera.id}/${action}`, {
        method: action === "test" ? "POST" : "PUT", headers: { "Content-Type": "application/json" },
        ...(action === "enabled" ? { body: JSON.stringify({ enabled: !camera.enabled }) } : {}),
      });
      if (!response.ok) throw new Error("Camera action failed");
      const result = await response.json();
      setMessage(action === "test" ? (result.connected ? "Connection test received a frame." : "Connection test did not receive a frame.") : "Camera state updated.");
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Camera action failed"); }
  };

  const refresh = async () => {
    try {
      const response = await fetch(`${apiBase}/cameras`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Camera list failed (${response.status})`);
      setCameras(await response.json());
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load cameras");
    }
  };

  useEffect(() => {
    const initial = setTimeout(refresh, 0);
    const timer = setInterval(refresh, 8000);
    return () => { clearTimeout(initial); clearInterval(timer); };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch(`${apiBase}/cameras`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, rtsp_url: rtspUrl, username, password, enabled: true }),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(body.detail || `Camera add failed (${response.status})`);
      setName("");
      setRtspUrl("");
      setUsername("");
      setPassword("");
      setMessage("Camera saved. ShopAware is attempting to connect in the background.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to add camera");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (camera: CameraRow) => {
    if (!window.confirm(`Remove camera "${camera.name}"?`)) return;
    setError("");
    const response = await fetch(`${apiBase}/cameras/${camera.id}`, { method: "DELETE" });
    if (!response.ok) {
      setError(`Unable to remove ${camera.name}`);
      return;
    }
    await refresh();
  };

  return (
    <div className="max-w-6xl mx-auto pb-10">
      <header className="mb-8">
        <h2 className="text-3xl font-bold tracking-tight mb-2">Cameras</h2>
        <p className="text-foreground/60">Add RTSP/NVR channels without embedding passwords in the camera URL.</p>
      </header>

      <div className="grid grid-cols-1 xl:grid-cols-[380px_1fr] gap-6">
        <section className="glass-panel p-5 h-fit">
          <div className="flex items-center gap-2 mb-5"><Plus className="w-5 h-5 text-brand" /><h3 className="font-semibold">Add camera</h3></div>
          <form onSubmit={submit} className="space-y-4">
            <label className="block">
              <span className="block text-xs text-foreground/55 mb-1.5">Camera name</span>
              <input className="input" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Liquor Aisle 1" />
            </label>
            <label className="block">
              <span className="block text-xs text-foreground/55 mb-1.5">RTSP URL (without credentials)</span>
              <input className="input font-mono text-sm" required value={rtspUrl} onChange={(e) => setRtspUrl(e.target.value)} placeholder="rtsp://10.0.0.25:554/Streaming/Channels/101" />
            </label>
            <label className="block">
              <span className="block text-xs text-foreground/55 mb-1.5">Username</span>
              <input className="input" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
            </label>
            <label className="block">
              <span className="block text-xs text-foreground/55 mb-1.5">Password</span>
              <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <button disabled={saving} className="btn btn-primary w-full" type="submit">{saving ? "Saving…" : "Save Camera"}</button>
          </form>
          <p className="mt-4 text-xs leading-5 text-foreground/45">
            ShopAware strips credentials embedded in the supplied URL, stores the username separately, and encrypts the password at rest.
          </p>
        </section>

        <section>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Configured cameras</h3>
            <button onClick={refresh} className="btn btn-secondary flex gap-2 items-center"><RefreshCw className="w-4 h-4" />Refresh</button>
          </div>

          {message && <div className="glass-panel border-green-500/25 text-green-200 p-3 mb-3 text-sm">{message}</div>}
          {error && <div className="glass-panel border-red-500/25 text-red-200 p-3 mb-3 text-sm">{error}</div>}

          {cameras.length === 0 ? (
            <div className="glass-panel p-10 text-center text-foreground/50"><Camera className="w-9 h-9 mx-auto mb-3" />No cameras configured.</div>
          ) : (
            <div className="space-y-3">
              {cameras.map((camera) => (
                <article key={camera.id} className="glass-panel p-4 flex gap-4 items-start justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <div className="font-semibold">{camera.name}</div>
                      <span className={`badge ${camera.status === "active" ? "text-green-400" : "text-amber-300"}`}>{camera.status}</span>
                    </div>
                    <div className="text-xs font-mono text-foreground/55 break-all">{camera.source}</div>
                    <div className="text-xs text-foreground/40 mt-2">
                      User: {camera.username || "none"} · Password: {camera.has_password ? "stored/encrypted" : "none"} · ROI points: {camera.roi_points?.length || 0}
                    </div>
                    <p className="text-xs mt-2">Last frame: {camera.last_frame_at ? new Date(camera.last_frame_at * 1000).toLocaleString() : "None"}</p>
                    <div className="flex flex-wrap gap-2 mt-3">
                      <button className="btn btn-secondary" onClick={() => cameraAction(camera, "enabled")}>{camera.enabled ? "Disable" : "Enable"}</button>
                      <button className="btn btn-secondary" onClick={() => cameraAction(camera, "test")}>Test connection</button>
                      <button className="btn btn-secondary" onClick={() => setZoneCamera(zoneCamera === camera.id ? null : camera.id)}>Zones</button>
                    </div>
                    {zoneCamera === camera.id && <ZoneEditor cameraId={camera.id} />}
                  </div>
                  <button className="btn btn-danger shrink-0" onClick={() => remove(camera)} title="Delete camera"><Trash2 className="w-4 h-4" /></button>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
