"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Camera, WifiOff } from "lucide-react";

type FrameItem = { camera_id: string; name: string; data: string };
type MultiFrame = { type: "multi_frame"; cameras: FrameItem[] };

export default function CameraGrid() {
  const [frames, setFrames] = useState<Record<string, FrameItem>>({});
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const wsUrl = useMemo(() => {
    if (process.env.NEXT_PUBLIC_WS_URL) return process.env.NEXT_PUBLIC_WS_URL;
    if (typeof window === "undefined") return "ws://127.0.0.1:8000/ws";
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const hostname = window.location.hostname;
    const local = hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
    // Development keeps the historical direct backend port. Production defaults
    // to same-origin /ws so Apache/Nginx can terminate TLS and proxy WebSockets.
    return local
      ? `${protocol}//${hostname}:8000/ws`
      : `${protocol}//${window.location.host}/ws`;
  }, []);

  useEffect(() => {
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      try {
        const socket = new WebSocket(wsUrl);
        socketRef.current = socket;

        socket.onopen = () => {
          setConnected(true);
          setError("");
        };

        socket.onmessage = (event) => {
          try {
            const payload: MultiFrame = JSON.parse(event.data);
            if (payload.type !== "multi_frame" || !Array.isArray(payload.cameras)) return;
            const next: Record<string, FrameItem> = {};
            for (const camera of payload.cameras) next[camera.camera_id] = camera;
            setFrames(next);
          } catch {
            setError("Received an invalid live-frame message.");
          }
        };

        socket.onerror = () => setError("Live stream connection error.");
        socket.onclose = () => {
          setConnected(false);
          if (!stopped) reconnectTimer.current = setTimeout(connect, 2500);
        };
      } catch {
        setConnected(false);
        if (!stopped) reconnectTimer.current = setTimeout(connect, 2500);
      }
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      socketRef.current?.close();
    };
  }, [wsUrl]);

  const items = Object.values(frames);

  return (
    <section>
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm text-foreground/55">
          {connected ? "Live WebSocket connected" : "Waiting for live connection"}
        </div>
        <span className={`badge ${connected ? "text-green-400" : "text-amber-400"}`}>
          {connected ? "Live" : "Reconnecting"}
        </span>
      </div>

      {error && <div className="mb-3 text-sm text-amber-300">{error}</div>}

      {items.length === 0 ? (
        <div className="glass-panel min-h-72 flex flex-col items-center justify-center text-center p-8 text-foreground/55">
          {connected ? <Camera className="w-10 h-10 mb-4" /> : <WifiOff className="w-10 h-10 mb-4" />}
          <div className="font-semibold text-foreground/80">No camera frames yet</div>
          <div className="text-sm mt-2 max-w-lg">
            Add an RTSP camera from the Cameras page. The backend will publish frames here once the stream and inference worker are running.
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {items.map((camera) => (
            <article key={camera.camera_id} className="glass-panel overflow-hidden">
              <div className="px-4 py-3 border-b border-glass-border flex justify-between items-center">
                <div className="font-medium">{camera.name}</div>
                <div className="text-xs text-foreground/45 font-mono">{camera.camera_id.slice(0, 8)}</div>
              </div>
              <div className="bg-black aspect-video flex items-center justify-center">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`data:image/jpeg;base64,${camera.data}`}
                  alt={`Live feed from ${camera.name}`}
                  className="w-full h-full object-contain"
                />
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
