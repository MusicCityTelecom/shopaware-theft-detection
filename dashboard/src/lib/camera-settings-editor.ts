/** Camera-scoped editor state. Late requests cannot replace another camera's draft. */
type RequestSettings<T> = (path: string, init?: RequestInit) => Promise<T>;
type EditorState<T> = {
  cameraId: string;
  settings: T | null;
  saved: T | null;
  loading: boolean;
  busy: boolean;
  error: string;
  message: string;
  dirty: boolean;
};

export class CameraSettingsEditor<T> {
  private state: EditorState<T> = {
    cameraId: "", settings: null, saved: null, loading: false, busy: false,
    error: "", message: "", dirty: false,
  };
  private listeners = new Set<() => void>();
  private generation = 0;
  private controller: AbortController | null = null;
  private request: RequestSettings<T | { settings: T }>;

  constructor(request: RequestSettings<T | { settings: T }>) {
    this.request = request;
  }

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  private update(change: Partial<EditorState<T>>) {
    this.state = { ...this.state, ...change };
    this.listeners.forEach(listener => listener());
  }

  cancel = () => {
    this.generation += 1;
    this.controller?.abort();
  };

  async select(cameraId: string) {
    if (this.state.busy) return;
    this.cancel();
    const generation = this.generation;
    this.controller = new AbortController();
    this.update({ cameraId, settings: null, saved: null, dirty: false,
      loading: !!cameraId, error: "", message: "" });
    if (!cameraId) return;
    try {
      const settings = await this.request(`/cameras/${encodeURIComponent(cameraId)}/mode-settings`, {
        signal: this.controller.signal,
      }) as T;
      if (generation !== this.generation) return;
      this.update({ settings: structuredClone(settings), saved: settings, loading: false });
    } catch (err) {
      if (generation !== this.generation) return;
      this.update({ loading: false, error: err instanceof Error ? err.message : "Unable to load mode settings" });
    }
  }

  edit = (change: (settings: T) => T) => {
    if (!this.state.settings || this.state.busy || this.state.loading) return;
    const settings = change(structuredClone(this.state.settings));
    this.update({ settings, dirty: JSON.stringify(settings) !== JSON.stringify(this.state.saved),
      message: "", error: "" });
  };

  revert = () => {
    if (!this.state.saved || this.state.busy) return;
    this.update({ settings: structuredClone(this.state.saved), dirty: false, error: "", message: "" });
  };

  async save() {
    const { cameraId, settings, busy, loading, dirty } = this.state;
    if (!cameraId || !settings || busy || loading || !dirty) return;
    const generation = this.generation;
    this.update({ busy: true, error: "", message: "" });
    try {
      const response = await this.request(`/cameras/${encodeURIComponent(cameraId)}/mode-settings`, {
        method: "PUT", body: JSON.stringify(settings),
      }) as { settings: T };
      if (generation !== this.generation) return;
      this.update({ settings: structuredClone(response.settings), saved: response.settings, dirty: false,
        message: "Per-camera mode settings saved and applied. No service restart is required." });
    } catch (err) {
      if (generation !== this.generation) return;
      this.update({ error: err instanceof Error ? err.message : "Unable to save mode settings" });
    } finally {
      if (generation === this.generation) this.update({ busy: false });
    }
  }
}
