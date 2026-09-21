type FaceTrackObservation = {
  id: string;
  camera_id: string;
  mode: string;
  subject_key: string;
  metadata?: { track_scope?: string };
};

export function groupFaceTracks<T extends FaceTrackObservation>(observations: T[]): T[][] {
  const grouped = new Map<string, T[]>();
  for (const item of observations) {
    if (item.mode !== "face_capture") continue;
    const scope = item.metadata?.track_scope;
    // Old numeric generation/track IDs can collide after restarts. Their
    // continuity cannot be recovered, so keep those captures separate.
    const key = typeof scope === "string" && /^[0-9a-f]{32}$/.test(scope)
      ? JSON.stringify([item.camera_id, scope, item.subject_key])
      : JSON.stringify([item.camera_id, "observation", item.id]);
    const group = grouped.get(key);
    if (group) group.push(item);
    else grouped.set(key, [item]);
  }
  return Array.from(grouped.values());
}
