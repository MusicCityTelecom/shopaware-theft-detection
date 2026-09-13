"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiBase, apiFetch } from "@/lib/api";

type Box = { class_id: number; x1: number; y1: number; x2: number; y2: number };
type Session = { id: string; name: string; split: string };
type Sample = { id: string; session_id: string; split: string; captured_at: number; width: number; height: number; reviewed: boolean; boxes: Box[] };
type Overview = { sessions: Session[]; samples: Sample[]; classes: string[]; used_bytes: number; max_bytes: number };
type Camera = { id: string; name: string; enabled: boolean; status: string };

async function request(path: string, method = "GET", body?: unknown) {
  const response = await apiFetch(`${apiBase}${path}`, { method, cache: "no-store", ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Request failed. Check the form and try again.");
  return result;
}

function LabelEditor({ sample, classes, onSaved, onBusy }: { sample: Sample; classes: string[]; onSaved: () => Promise<void>; onBusy: (busy: boolean) => void }) {
  const [boxes, setBoxes] = useState<Box[]>(sample.boxes);
  const [start, setStart] = useState<[number, number] | null>(null);
  const [classId, setClassId] = useState(39);
  const [reviewed, setReviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  async function save() {
    setBusy(true); onBusy(true); setError("");
    try { await request(`/training/samples/${sample.id}`, "PUT", { boxes, reviewed: true }); await onSaved(); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to save labels"); }
    finally { setBusy(false); onBusy(false); }
  }
  return <section className="glass-panel p-5 space-y-4">
    <h3 className="font-semibold">Label this example · {sample.split} · {sample.reviewed ? "Saved review" : "Needs labels"}</h3>
    <p className="text-sm text-foreground/65">Choose an object class, then click two opposite corners around it. Label every visible object from the class list, including partly hidden objects where recognizable. Remove and redraw a box to correct it.</p>
    <label className="block">Object class<select aria-label="Object class" className="input mt-1" value={classId} onChange={e => setClassId(Number(e.target.value))}>{classes.map((name, i) => <option key={name} value={i}>{name}</option>)}</select></label>
    <div className="relative bg-black" style={{ aspectRatio: `${sample.width} / ${sample.height}` }}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={`${apiBase}/training/samples/${sample.id}/image`} alt="Captured training example" className="w-full h-full" onLoad={() => setLoaded(true)} onError={() => { setLoaded(false); setError("Image unavailable. Refresh the example list."); }} />
      <svg aria-label="Draw object boxes" className="absolute inset-0 w-full h-full touch-none" viewBox={`0 0 ${sample.width} ${sample.height}`} preserveAspectRatio="none" onPointerDown={e => {
        if (!loaded || busy) return;
        const r = e.currentTarget.getBoundingClientRect();
        const x = Math.max(0, Math.min(1, (e.clientX-r.left)/r.width)), y = Math.max(0, Math.min(1, (e.clientY-r.top)/r.height));
        if (!start) { setStart([x,y]); return; }
        const [sx,sy] = start;
        if (Math.abs(x-sx) > .002 && Math.abs(y-sy) > .002 && boxes.length < 200) {
          setBoxes([...boxes, { class_id: classId, x1: Math.min(sx,x), y1: Math.min(sy,y), x2: Math.max(sx,x), y2: Math.max(sy,y) }]); setReviewed(false);
        }
        setStart(null);
      }}>
        {boxes.map((b,i) => <g key={i} pointerEvents="none"><rect x={b.x1*sample.width} y={b.y1*sample.height} width={(b.x2-b.x1)*sample.width} height={(b.y2-b.y1)*sample.height} stroke="#fbbf24" strokeWidth="2" fill="#fbbf2415" /><text x={b.x1*sample.width+3} y={b.y1*sample.height+15} fill="#fbbf24" fontSize="14">{i+1}. {classes[b.class_id]}</text></g>)}
        {start && <circle cx={start[0]*sample.width} cy={start[1]*sample.height} r="5" fill="#22d3ee" />}
      </svg>
    </div>
    <div className="flex flex-wrap gap-2"><button className="btn btn-secondary" onClick={() => setStart(null)}>Cancel corner</button>{boxes.map((b,i) => <button key={i} className="btn btn-secondary" disabled={busy} onClick={() => { setBoxes(boxes.filter((_,j) => i !== j)); setReviewed(false); }}>Remove {i+1}: {classes[b.class_id]}</button>)}</div>
    <label className="flex gap-2 items-start text-sm"><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} />{boxes.length ? "I reviewed every visible object and these boxes are complete." : "I reviewed this image: it contains no objects from the class list (background example)."}</label>
    <button className="btn btn-primary" disabled={!reviewed || busy || !loaded || start !== null} onClick={save}>{busy ? "Saving…" : "Save reviewed labels"}</button>
    {error && <p role="alert" className="text-red-300">{error}</p>}
  </section>;
}

export default function TrainingPage() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [cameraId, setCameraId] = useState("");
  const [data, setData] = useState<Overview | null>(null);
  const [sessionId, setSessionId] = useState("");
  const [sampleId, setSampleId] = useState("");
  const [name, setName] = useState("");
  const [split, setSplit] = useState("train");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => { request("/cameras").then((rows: Camera[]) => {
    setCameras(rows);
    const selected = new URLSearchParams(window.location.search).get("camera");
    setCameraId(rows.some(c => c.id === selected) ? selected! : rows[0]?.id || "");
  }).catch(e => setError(e.message)); }, []);
  useEffect(() => {
    if (!cameraId) return;
    let current = true;
    request(`/training?camera_id=${encodeURIComponent(cameraId)}`).then(result => { if (current) setData(result); }).catch(e => { if (current) setError(e.message); });
    return () => { current = false; };
  }, [cameraId]);
  async function refresh() { setData(await request(`/training?camera_id=${encodeURIComponent(cameraId)}`)); }
  async function action(fn: () => Promise<void>) {
    setBusy(true); setError(""); setMessage("");
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Training action failed"); }
    finally { setBusy(false); }
  }
  const sample = data?.samples.find(s => s.id === sampleId);
  const camera = cameras.find(c => c.id === cameraId);
  const ready = ["train","val","test"].every(split => data?.samples.some(s => s.split === split && s.reviewed && s.boxes.length));
  return <div className="max-w-6xl mx-auto pb-10 space-y-6">
    <header><h2 className="text-3xl font-bold mb-2">Train with your camera</h2><p className="text-foreground/65">Collect examples, label objects, then train and evaluate a separate model. Camera selection chooses the dataset source; it does not start automatic learning.</p></header>
    <details className="glass-panel p-5" open><summary className="font-semibold cursor-pointer">How training works</summary><ol className="list-decimal pl-5 mt-3 space-y-2 text-sm text-foreground/75">
      <li>First use <Link href="/cameras" className="text-brand underline">camera zones</Link> and <Link href="/settings" className="text-brand underline">risk settings</Link> to reduce avoidable alerts. Reviewing an incident does not retrain a model.</li>
      <li>Choose a camera below. Create a collection session for a particular visit/time period. Put whole, separate scenes into Training, Validation, and Test sessions. Keep adjacent frames and the same event in one split.</li>
      <li>Capture varied views, lighting, occlusion, normal activity and empty scenes. Draw accurate boxes and explicitly review every example. The export minimum is a format check; a handful of examples is not a qualified dataset.</li>
      <li>Export the reviewed dataset and run the commands below on a training computer. Nothing is uploaded to a cloud service. Captured images are private camera footage; protect downloaded copies.</li>
      <li>Compare the resulting model against the baseline on held-out scenes and real incident behavior before activation. This workflow trains COCO object detection; temporal activity recognition and pose training need different datasets.</li>
    </ol><p className="text-sm mt-3">See the <a className="text-brand underline" href="https://docs.ultralytics.com/datasets/detect/" target="_blank" rel="noreferrer">Ultralytics dataset guide</a> and the project’s <code>docs/TRAINING.md</code> for the full procedure.</p></details>
    {error && <p role="alert" className="glass-panel p-3 text-red-300">{error}</p>}{message && <p role="status" className="text-green-300">{message}</p>}
    {!cameras.length ? <p>Add a camera on the <Link href="/cameras" className="text-brand underline">Cameras page</Link> to begin.</p> : <>
      <section className="glass-panel p-5 space-y-4"><label className="block">Training camera<select className="input mt-1" disabled={busy} value={cameraId} onChange={e => { setCameraId(e.target.value); setData(null); setSessionId(""); setSampleId(""); setError(""); setMessage(""); }}>{cameras.map(c => <option key={c.id} value={c.id}>{c.name} · {c.status}</option>)}</select></label>
        <form className="flex flex-wrap gap-3 items-end" onSubmit={e => { e.preventDefault(); void action(async () => { const session = await request("/training/sessions", "POST", { camera_id: cameraId, name, split }); setSessionId(session.id); setName(""); await refresh(); }); }}>
          <label className="grow">New session name<input className="input mt-1" required maxLength={80} value={name} onChange={e => setName(e.target.value)} placeholder="Monday morning — aisle visit" /></label>
          <label>Use for<select aria-label="Use for" className="input mt-1" value={split} onChange={e => setSplit(e.target.value)}><option value="train">Training</option><option value="val">Validation</option><option value="test">Test (held out)</option></select></label>
          <button className="btn btn-secondary" disabled={busy || !data}>Create session</button>
        </form>
        <label className="block">Capture into session<select className="input mt-1" value={sessionId} disabled={busy} onChange={e => setSessionId(e.target.value)}><option value="">Choose a collection session</option>{data?.sessions.map(s => <option key={s.id} value={s.id}>{s.name} · {s.split}</option>)}</select></label>
        <div className="flex flex-wrap gap-3"><button className="btn btn-primary" disabled={busy || !sessionId || !camera?.enabled} onClick={() => action(async () => { const result = await request(`/training/sessions/${sessionId}/capture`, "POST"); await refresh(); setSampleId(result.id); setMessage("Captured one example. Draw boxes, then save your review."); })}>Capture current frame</button>
          <button className="btn btn-danger" disabled={busy || !sessionId} onClick={() => { if (window.confirm("Delete this collection session and its captured examples?")) void action(async () => { await request(`/training/sessions/${sessionId}`, "DELETE"); setSessionId(""); setSampleId(""); await refresh(); }); }}>Delete session</button></div>
        {!camera?.enabled && <p className="text-sm text-amber-300">Enable this camera on the Cameras page to capture. Existing examples can still be reviewed.</p>}
        <p className="text-xs text-foreground/60">Captures are original frames without detection overlays, resized to at most 1280 pixels. Ignore zones affect inference, not these images. All cameras share a 64 MiB / 1,000-example limit; deleting examples frees logical capacity. Camera deletion also deletes its training examples.</p>
        {data && <p className="text-sm">Training image storage: {(data.used_bytes/1024/1024).toFixed(1)} / 64 MiB · {data.samples.length} examples for this camera</p>}
      </section>
      {!!data?.samples.length && <div className="grid lg:grid-cols-[260px_1fr] gap-5 items-start">
        <section className="glass-panel p-4 space-y-3"><h3 className="font-semibold">Captured examples</h3><div className="max-h-[520px] overflow-y-auto space-y-2">{data.samples.map(s => <button key={s.id} disabled={busy} className={`block text-left w-full p-3 rounded border ${sampleId === s.id ? "border-brand bg-brand/10" : "border-white/10"}`} onClick={() => { setSampleId(s.id); setMessage(""); }}><span className="block text-sm">{new Date(s.captured_at*1000).toLocaleString()}</span><span className="text-xs text-foreground/60">{s.split} · {s.reviewed ? `${s.boxes.length} boxes · reviewed` : "Needs review"}</span></button>)}</div>
          <button className="btn btn-danger" disabled={busy || !sampleId} onClick={() => { if (window.confirm("Delete this captured example?")) void action(async () => { await request(`/training/samples/${sampleId}`, "DELETE"); setSampleId(""); await refresh(); }); }}>Delete selected example</button></section>
        {sample ? <LabelEditor key={`${sample.id}-${sample.reviewed}-${JSON.stringify(sample.boxes)}`} sample={sample} classes={data.classes} onBusy={setBusy} onSaved={async () => { await refresh(); setMessage("Reviewed labels saved. This example is eligible for export."); }} /> : <p>Select an example to label it.</p>}
      </div>}
      <section className="glass-panel p-5 space-y-3"><h3 className="font-semibold">Export and train</h3><p className="text-sm text-foreground/65">Export requires at least one reviewed example with boxes in each split. Unreviewed examples are excluded. Sessions stay in their original split.</p>
        <button className="btn btn-primary" disabled={!ready || busy} onClick={() => action(async () => { const r = await apiFetch(`${apiBase}/training/export?camera_id=${encodeURIComponent(cameraId)}`); if (!r.ok) { const b = await r.json(); throw new Error(b.detail || "Export failed"); } const url = URL.createObjectURL(await r.blob()); const a = document.createElement("a"); a.href = url; a.download = "shopaware-camera-dataset.zip"; a.click(); setTimeout(() => URL.revokeObjectURL(url), 30000); setMessage("Dataset downloaded. Extract it into training-data/camera and follow the commands below."); })}>Download reviewed dataset</button>
        <p className="text-sm">Extract into <code>training-data/camera</code> in your ShopAware checkout. In its Python environment:</p>
        <pre className="bg-black/30 p-4 rounded text-xs overflow-x-auto">{`python -m tools.train_camera --data training-data/camera/data.yaml --check-only\npython -m tools.train_camera --data training-data/camera/data.yaml --device cpu --epochs 50`}</pre>
        <p className="text-sm text-foreground/65">The second command starts training and may download YOLO26 weights. For an installed NVIDIA/PyTorch CUDA environment, use <code>--device 0</code>. Run it on separate hardware or stop camera analysis first to avoid resource contention. Training duration depends on your hardware and dataset.</p>
        <p className="text-sm text-foreground/65">The command saves a new checkpoint and a held-out test report. It does not activate the model. After qualification, set its server-side path in Settings → Detection model and restart the backend. This currently changes the detector for all cameras. Keep the pose model unchanged and disable the optional specialized-model override to use the new detector. Roll back with <code>yolo26n.pt</code>.</p>
      </section>
    </>}
  </div>;
}
