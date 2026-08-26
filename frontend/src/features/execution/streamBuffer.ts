/** rAF-batched stream text buffer — avoids setState on every token. */

type Listener = (text: string) => void;

class StreamBuffer {
  private buffers = new Map<string, string>();
  private pending = new Map<string, string>();
  private listeners = new Map<string, Set<Listener>>();
  private globalListeners = new Set<() => void>();
  private raf: number | null = null;

  append(nodeId: string, delta: string): void {
    this.pending.set(nodeId, (this.pending.get(nodeId) ?? "") + delta);
    this.schedule();
  }

  set(nodeId: string, text: string): void {
    this.buffers.set(nodeId, text);
    this.pending.delete(nodeId);
    this.emit(nodeId);
  }

  get(nodeId: string): string {
    const base = this.buffers.get(nodeId) ?? "";
    const pend = this.pending.get(nodeId) ?? "";
    return base + pend;
  }

  clear(nodeId?: string): void {
    if (nodeId) {
      this.buffers.delete(nodeId);
      this.pending.delete(nodeId);
      this.emit(nodeId);
    } else {
      this.buffers.clear();
      this.pending.clear();
      this.globalListeners.forEach((l) => l());
    }
  }

  subscribe(nodeId: string, listener: Listener): () => void {
    let set = this.listeners.get(nodeId);
    if (!set) {
      set = new Set();
      this.listeners.set(nodeId, set);
    }
    set.add(listener);
    return () => set!.delete(listener);
  }

  subscribeAll(listener: () => void): () => void {
    this.globalListeners.add(listener);
    return () => this.globalListeners.delete(listener);
  }

  private schedule(): void {
    if (this.raf != null) return;
    this.raf = window.requestAnimationFrame(() => {
      this.raf = null;
      const keys = [...this.pending.keys()];
      for (const nodeId of keys) {
        const delta = this.pending.get(nodeId) ?? "";
        if (!delta) continue;
        this.buffers.set(nodeId, (this.buffers.get(nodeId) ?? "") + delta);
        this.pending.delete(nodeId);
        this.emit(nodeId);
      }
      this.globalListeners.forEach((l) => l());
    });
  }

  private emit(nodeId: string): void {
    const text = this.buffers.get(nodeId) ?? "";
    this.listeners.get(nodeId)?.forEach((l) => l(text));
    this.globalListeners.forEach((l) => l());
  }
}

export const streamBuffer = new StreamBuffer();
