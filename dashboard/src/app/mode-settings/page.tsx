"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { SlidersHorizontal } from "lucide-react";
import { apiJson } from "@/lib/admin";
import { CameraSettingsEditor } from "@/lib/camera-settings-editor";

type CameraRow = { id: string; name: string; modes?: string[]; group_name?: string | null };

type ModeSettings = {
  shoplifting: {
    risk_threshold: number;
    risk_window_seconds: number;
    candidate_cooldown_seconds: number;
    loitering_seconds: number;
  };
  vehicle_break_in: {
    risk_threshold: number;
    dwell_seconds: number;
    required_access_interactions: number;
    access_interval_seconds: number;
    candidate_cooldown_seconds: number;
  };
  lpr: {
    observation_cooldown_seconds: number;
    min_ocr_confidence: number;
    min_plate_chars: number;
    max_plate_chars: number;
  };
  face_capture: {
    capture_cooldown_seconds: number;
    max_images_per_track: number;
    min_quality: number;
  };
};

const beta4Defaults: ModeSettings = {
  shoplifting: { risk_threshold: 65, risk_window_seconds: 10, candidate_cooldown_seconds: 60, loitering_seconds: 12 },
  vehicle_break_in: { risk_threshold: 65, dwell_seconds: 12, required_access_interactions: 3, access_interval_seconds: 1.5, candidate_cooldown_seconds: 90 },
  lpr: { observation_cooldown_seconds: 60, min_ocr_confidence: 0.2, min_plate_chars: 4, max_plate_chars: 10 },
  face_capture: { capture_cooldown_seconds: 8, max_images_per_track: 5, min_quality: 0 },
};

function NumberField({ label, value, min, max, step = "any", onChange, suffix, help }: {
  label: string; value: number; min: number; max: number; step?: number | "any";
  onChange: (value: number) => void; suffix?: string; help?: string;
}) {
  return <label className="block">
    <span className="block text-xs text-foreground/60 mb-1.5">{label}</span>
    <div className="flex items-center gap-2">
      <input className="input" type="number" required value={Number.isNaN(value) ? "" : value} min={min} max={max} step={step}
        onChange={event => onChange(event.target.valueAsNumber)} />
      {suffix && <span className="text-xs text-foreground/45 min-w-12">{suffix}</span>}
    </div>
    {help && <span className="block text-[11px] leading-4 text-foreground/40 mt-1">{help}</span>}
  </label>;
}

function ModePanel({ title, enabled, children, note }: {
  title: string; enabled: boolean; children: React.ReactNode; note: string;
}) {
  return <section className="glass-panel p-5">
    <div className="flex items-center justify-between gap-3 mb-2">
      <h3 className="font-semibold">{title}</h3>
      <span className={`badge ${enabled ? "text-green-300" : "text-foreground/40"}`}>{enabled ? "Enabled on camera" : "Currently disabled"}</span>
    </div>
    <p className="text-xs leading-5 text-foreground/45 mb-4">{note}</p>
    <div className="grid sm:grid-cols-2 gap-4">{children}</div>
  </section>;
}

export default function ModeSettingsPage() {
  const [cameras, setCameras] = useState<CameraRow[]>([]);
  const [editor] = useState(() => new CameraSettingsEditor<ModeSettings>(apiJson));
  const { cameraId, settings, busy, loading, dirty, message, error } = useSyncExternalStore(
    editor.subscribe, editor.getSnapshot, editor.getSnapshot,
  );
  const [cameraError, setCameraError] = useState("");

  const camera = useMemo(() => cameras.find(item => item.id === cameraId), [cameras, cameraId]);
  const enabled = (mode: string) => camera?.modes?.includes(mode) ?? false;

  useEffect(() => {
    let active = true;
    apiJson<CameraRow[]>("/cameras")
      .then(rows => {
        if (!active) return;
        setCameras(rows);
        if (rows.length) void editor.select(rows[0].id);
      })
      .catch(err => {
        if (active) setCameraError(err instanceof Error ? err.message : "Unable to load cameras");
      });
    return () => { active = false; editor.cancel(); };
  }, [editor]);

  useEffect(() => {
    if (!dirty && !busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, busy]);

  const chooseCamera = (id: string) => {
    if (id === cameraId || busy) return;
    if (dirty && !window.confirm("Discard unsaved changes for this camera?")) return;
    void editor.select(id);
  };

  const setSection = <K extends keyof ModeSettings>(section: K, key: keyof ModeSettings[K], value: number) => {
    editor.edit(current => ({ ...current, [section]: { ...current[section], [key]: value } }));
  };

  return <div className="max-w-6xl mx-auto pb-12">
    <header className="mb-7">
      <div className="flex items-center gap-3 mb-2"><SlidersHorizontal className="w-7 h-7 text-brand" /><h2 className="text-3xl font-bold tracking-tight">Camera Mode Settings</h2></div>
      <p className="text-foreground/60 max-w-3xl">Tune each analytics mode independently for each camera. These are heuristic thresholds and capture controls—not probabilities or proof of an event.</p>
    </header>

    <section className="glass-panel p-5 mb-5">
      <label className="block max-w-xl">
        <span className="block text-xs text-foreground/60 mb-1.5">Camera</span>
        <select className="input" value={cameraId} disabled={busy} onChange={event => chooseCamera(event.target.value)}>
          {!cameras.length && <option value="">No cameras configured</option>}
          {cameras.map(item => <option value={item.id} key={item.id}>{item.name}{item.group_name ? ` — ${item.group_name}` : ""}</option>)}
        </select>
      </label>
      {camera && <div className="flex flex-wrap gap-1.5 mt-3">{camera.modes?.map(mode => <span className="badge text-brand" key={mode}>{mode.replaceAll("_", " ")}</span>)}</div>}
    </section>

    {message && <div role="status" className="glass-panel border-green-500/25 text-green-200 p-3 mb-4 text-sm">{message}</div>}
    {(error || cameraError) && <div role="alert" className="glass-panel border-red-500/25 text-red-200 p-3 mb-4 text-sm">{error || cameraError}</div>}

    {!settings ? <div className="glass-panel p-8 text-foreground/50">{loading ? "Loading settings…" : cameraId ? <>Settings could not be loaded. <button className="btn btn-secondary" type="button" onClick={() => void editor.select(cameraId)}>Retry</button></> : "Add a camera before configuring analytics."}</div> : <form onSubmit={event => { event.preventDefault(); void editor.save(); }}>
      <fieldset disabled={busy} className="grid lg:grid-cols-2 gap-4">
        <ModePanel title="Shoplifting" enabled={enabled("shoplifting")} note="Controls how multiple retail-interaction signals accumulate into a human-review candidate.">
          <NumberField label="Risk threshold" value={settings.shoplifting.risk_threshold} min={Number.MIN_VALUE} max={100} onChange={v => setSection("shoplifting", "risk_threshold", v)} suffix="points" help="Higher values require more simultaneous signals." />
          <NumberField label="Signal window" value={settings.shoplifting.risk_window_seconds} min={Number.MIN_VALUE} max={300} onChange={v => setSection("shoplifting", "risk_window_seconds", v)} suffix="seconds" help="How long behavior signals remain active together." />
          <NumberField label="Candidate cooldown" value={settings.shoplifting.candidate_cooldown_seconds} min={Number.MIN_VALUE} max={3600} onChange={v => setSection("shoplifting", "candidate_cooldown_seconds", v)} suffix="seconds" help="Suppresses repeated incidents for the same tracked person." />
          <NumberField label="Loitering threshold" value={settings.shoplifting.loitering_seconds} min={Number.MIN_VALUE} max={3600} onChange={v => setSection("shoplifting", "loitering_seconds", v)} suffix="seconds" help="Dwell time before excessive-dwell becomes a signal." />
        </ModePanel>

        <ModePanel title="Vehicle break-in" enabled={enabled("vehicle_break_in")} note="Creates review candidates from sustained person/vehicle proximity and repeated entry-area hand interactions.">
          <NumberField label="Risk threshold" value={settings.vehicle_break_in.risk_threshold} min={Number.MIN_VALUE} max={100} onChange={v => setSection("vehicle_break_in", "risk_threshold", v)} suffix="points" />
          <NumberField label="Near-vehicle dwell" value={settings.vehicle_break_in.dwell_seconds} min={Number.MIN_VALUE} max={3600} onChange={v => setSection("vehicle_break_in", "dwell_seconds", v)} suffix="seconds" />
          <NumberField label="Required access interactions" value={settings.vehicle_break_in.required_access_interactions} min={1} max={50} onChange={v => setSection("vehicle_break_in", "required_access_interactions", Math.round(v))} suffix="events" help="Separated hand-near-entry interactions before that signal activates." />
          <NumberField label="Access interaction interval" value={settings.vehicle_break_in.access_interval_seconds} min={Number.MIN_VALUE} max={60} onChange={v => setSection("vehicle_break_in", "access_interval_seconds", v)} suffix="seconds" help="Prevents every processed frame from counting as a separate attempt." />
          <NumberField label="Candidate cooldown" value={settings.vehicle_break_in.candidate_cooldown_seconds} min={Number.MIN_VALUE} max={3600} onChange={v => setSection("vehicle_break_in", "candidate_cooldown_seconds", v)} suffix="seconds" />
        </ModePanel>

        <ModePanel title="LPR" enabled={enabled("lpr")} note="Controls conservative OCR acceptance and duplicate suppression. Plate text still requires human verification.">
          <NumberField label="Same-plate cooldown" value={settings.lpr.observation_cooldown_seconds} min={0} max={86400} onChange={v => setSection("lpr", "observation_cooldown_seconds", v)} suffix="seconds" />
          <NumberField label="Minimum OCR confidence" value={Math.round(settings.lpr.min_ocr_confidence * 100)} min={0} max={100} onChange={v => setSection("lpr", "min_ocr_confidence", v / 100)} suffix="percent" help="Raise this to reduce low-confidence plate candidates." />
          <NumberField label="Minimum plate characters" value={settings.lpr.min_plate_chars} min={1} max={settings.lpr.max_plate_chars} step={1} onChange={v => setSection("lpr", "min_plate_chars", Math.round(v))} />
          <NumberField label="Maximum plate characters" value={settings.lpr.max_plate_chars} min={settings.lpr.min_plate_chars} max={16} step={1} onChange={v => setSection("lpr", "max_plate_chars", Math.round(v))} />
        </ModePanel>

        <ModePanel title="Face Capture" enabled={enabled("face_capture")} note="Stores anonymous face crops under one continuous camera track. This does not enable face recognition or identity matching.">
          <NumberField label="Capture cooldown" value={settings.face_capture.capture_cooldown_seconds} min={0} max={3600} onChange={v => setSection("face_capture", "capture_cooldown_seconds", v)} suffix="seconds" />
          <NumberField label="Max images per track" value={settings.face_capture.max_images_per_track} min={1} max={100} onChange={v => setSection("face_capture", "max_images_per_track", Math.round(v))} suffix="images" />
          <NumberField label="Minimum image quality" value={Math.round(settings.face_capture.min_quality * 100)} min={0} max={100} onChange={v => setSection("face_capture", "min_quality", v / 100)} suffix="percent" help="Composite crop-size/sharpness gate. Higher values save fewer, clearer candidates." />
        </ModePanel>
      </fieldset>

      <div className="glass-panel p-4 mt-5 flex flex-wrap gap-3 items-center justify-between">
        <p className="text-xs text-foreground/45 max-w-2xl">Changing these values resets the affected mode state used for short-lived scoring and cooldowns, but does not delete incidents, observations, camera assignments, or training data.</p>
        <div className="flex flex-wrap items-center gap-2">
          {dirty && <span className="text-xs text-amber-200" role="status">Unsaved changes</span>}
          <button className="btn btn-secondary" type="button" disabled={busy || !dirty} onClick={editor.revert}>Discard changes</button>
          <button className="btn btn-secondary" type="button" disabled={busy} onClick={() => editor.edit(() => structuredClone(beta4Defaults))}>Restore beta.4 defaults</button>
          <button className="btn btn-primary" type="submit" disabled={busy || !dirty}>{busy ? "Saving…" : "Save and apply"}</button>
        </div>
      </div>
    </form>}
  </div>;
}
