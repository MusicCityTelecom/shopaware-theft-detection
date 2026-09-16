"use client";

import { apiBase, apiFetch as fetch } from "@/lib/api";

import { FormEvent, useEffect, useState } from "react";
import { Camera, Plus, RefreshCw, Trash2 } from "lucide-react";
import ZoneEditor from "@/components/ZoneEditor";
import Link from "next/link";
import { useSession } from "@/components/AuthGate";
import { apiJson, CustomerGroup } from "@/lib/admin";

type CameraRow = {
  id: string;
  group_id: string | null;
  group_name: string | null;
  name: string;
  rtsp_url: string;
  username: string;
  has_password: boolean;
  source: string;
  status: string;
  enabled: boolean;
  last_frame_at?: number;
  roi_points: number[][];
  modes: string[];
};

const modeOptions = [
  { id: "shoplifting", label: "Shoplifting", detail: "Retail interaction and concealment candidates" },
  { id: "vehicle_break_in", label: "Vehicle break-in", detail: "Person/vehicle interaction candidates" },
  { id: "lpr", label: "LPR", detail: "Plate OCR snapshots and estimated vehicle color" },
  { id: "face_capture", label: "Face capture", detail: "Anonymous face snapshots grouped by continuous track" },
];


export default function CamerasPage() {
  const isAdmin = useSession()?.role === "admin";
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  const [groupId, setGroupId] = useState("");
  const [groupFilter, setGroupFilter] = useState("");
  async function moveGroup(camera: CameraRow, value: string) {
    if (!window.confirm("Move this camera to another customer group? Individual user grants will be cleared and earlier incidents will become admin-only. Reassign any needed user access afterward.")) return;
    try {
      await apiJson(`/cameras/${camera.id}/group`, { method: "PUT", body: JSON.stringify({ group_id: value || null }) });
      setMessage("Customer group updated. Review user access for this camera."); await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Group update failed"); }
  }
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [name, setName] = useState("");
  const [rtspUrl, setRtspUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [newModes, setNewModes] = useState<string[]>(["shoplifting"]);
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

  const saveModes = async (camera: CameraRow, modes: string[]) => {
    if (!modes.length) { setError("Each camera must have at least one mode."); return; }
    try {
      await apiJson(`/cameras/${camera.id}/modes`, { method: "PUT", body: JSON.stringify({ modes }) });
      setMessage(`Modes updated for ${camera.name}.`);
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Mode update failed"); }
  };

  const refresh = async () => {
    try {
      const response = await fetch(`${apiBase}/cameras`, { cache: "no-store" });
      if (!response.ok) throw new Error(`Camera list failed (${response.status})`);
      setCameras(await response.json());
      setGroups(await apiJson<CustomerGroup[]>("/groups"));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load cameras");
    }
  };

  useEffect(() => {
    const initial = setTimeout(() => { setGroupFilter(new URLSearchParams(window.location.search).get("group") || ""); refresh(); }, 0);
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
        body: JSON.stringify({ name, rtsp_url: rtspUrl, username, password, enabled: true, group_id: groupId || null, modes: newModes }),
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
        <p className="text-foreground/60">{isAdmin ? "Manage camera connections and customer groups." : "Cameras assigned to your account. View their live feeds from Overview."}</p>
      </header>

      <label className="block mb-5 max-w-md">Filter by customer<select className="input" value={groupFilter} onChange={e => setGroupFilter(e.target.value)}><option value="">All accessible cameras</option><option value="ungrouped">Ungrouped</option>{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
      <div className={`grid grid-cols-1 ${isAdmin ? "xl:grid-cols-[380px_1fr]" : ""} gap-6`}>
        {isAdmin && <section className="glass-panel p-5 h-fit">
          <div className="flex items-center gap-2 mb-5"><Plus className="w-5 h-5 text-brand" /><h3 className="font-semibold">Add camera</h3></div>
          <form onSubmit={submit} className="space-y-4">
            <label className="block">Customer group<select className="input" value={groupId} onChange={e => setGroupId(e.target.value)}><option value="">Ungrouped</option>{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
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
            <fieldset className="space-y-2">
              <legend className="text-xs text-foreground/55 mb-2">Camera modes (choose one or more)</legend>
              {modeOptions.map(mode => <label key={mode.id} className="flex gap-2 items-start text-sm">
                <input type="checkbox" className="mt-1" checked={newModes.includes(mode.id)} onChange={e => setNewModes(e.target.checked ? [...newModes, mode.id] : newModes.filter(value => value !== mode.id))} />
                <span><span className="font-medium">{mode.label}</span><span className="block text-xs text-foreground/45">{mode.detail}</span></span>
              </label>)}
            </fieldset>
            <button disabled={saving} className="btn btn-primary w-full" type="submit">{saving ? "Saving…" : "Save Camera"}</button>
          </form>
          <p className="mt-4 text-xs leading-5 text-foreground/45">
            ShopAware strips credentials embedded in the supplied URL, stores the username separately, and encrypts the password at rest.
          </p>
        </section>}

        <section>
          <div className="flex items-center justify-between mb-3">
            <h3 className="font-semibold">Configured cameras</h3>
            <button onClick={refresh} className="btn btn-secondary flex gap-2 items-center"><RefreshCw className="w-4 h-4" />Refresh</button>
          </div>

          {message && <div className="glass-panel border-green-500/25 text-green-200 p-3 mb-3 text-sm">{message}</div>}
          {error && <div className="glass-panel border-red-500/25 text-red-200 p-3 mb-3 text-sm">{error}</div>}

          {cameras.filter(c => !groupFilter || (c.group_id || "ungrouped") === groupFilter).length === 0 ? (
            <div className="glass-panel p-10 text-center text-foreground/50"><Camera className="w-9 h-9 mx-auto mb-3" />No cameras configured.</div>
          ) : (
            <div className="space-y-3">
              {cameras.filter(c => !groupFilter || (c.group_id || "ungrouped") === groupFilter).map((camera) => (
                <article key={camera.id} className="glass-panel p-4 flex gap-4 items-start justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-2">
                      <div className="font-semibold">{camera.name}</div>
                      <span className={`badge ${camera.status === "active" ? "text-green-400" : "text-amber-300"}`}>{camera.status}</span>
                    </div>
                    <p className="text-sm mb-2">{camera.group_name || "Ungrouped"}</p>
                    {isAdmin && <><div className="text-xs font-mono text-foreground/55 break-all">{camera.source}</div>
                    <div className="text-xs text-foreground/40 mt-2">
                      User: {camera.username || "none"} · Password: {camera.has_password ? "stored/encrypted" : "none"} · ROI points: {camera.roi_points?.length || 0}
                    </div></>}
                    <p className="text-xs mt-2">Last frame: {camera.last_frame_at ? new Date(camera.last_frame_at * 1000).toLocaleString() : "None"}</p>
                    <div className="flex flex-wrap gap-1.5 mt-3">{camera.modes?.map(mode => <span className="badge text-brand" key={mode}>{mode.replaceAll("_", " ")}</span>)}</div>
                    {isAdmin && <><label className="block text-sm mt-3">Customer group for {camera.name}<select className="input" value={camera.group_id || ""} onChange={e => moveGroup(camera, e.target.value)}><option value="">Ungrouped</option>{groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></label>
                    <fieldset className="grid sm:grid-cols-2 gap-2 mt-3 p-3 rounded-lg bg-black/15 border border-glass-border">
                      <legend className="text-sm px-1">Enabled analytics modes</legend>
                      {modeOptions.map(mode => <label key={mode.id} className="flex gap-2 items-center text-sm">
                        <input type="checkbox" checked={camera.modes?.includes(mode.id)} onChange={e => saveModes(camera, e.target.checked ? [...camera.modes, mode.id] : camera.modes.filter(value => value !== mode.id))} />
                        {mode.label}
                      </label>)}
                    </fieldset>
                    <div className="flex flex-wrap gap-2 mt-3">
                      <button className="btn btn-secondary" onClick={() => cameraAction(camera, "enabled")}>{camera.enabled ? "Disable" : "Enable"}</button>
                      <button className="btn btn-secondary" onClick={() => cameraAction(camera, "test")}>Test connection</button>
                      <button className="btn btn-secondary" onClick={() => setZoneCamera(zoneCamera === camera.id ? null : camera.id)}>Zones</button>
                      <Link className="btn btn-secondary" href={`/training?camera=${encodeURIComponent(camera.id)}`}>Train with this camera</Link>
                    </div>
                    {zoneCamera === camera.id && <ZoneEditor cameraId={camera.id} />}</>}
                  </div>
                  {isAdmin && <button className="btn btn-danger shrink-0" onClick={() => remove(camera)} title="Delete camera"><Trash2 className="w-4 h-4" /></button>}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
