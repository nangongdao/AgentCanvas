/** SSE client with exponential backoff reconnect + Last-Event-ID resume. */

import type {
  ExecutionEventContract,
  ExecutionEventType,
} from "@/api/contracts";

export type SseHandler = (event: MessageEvent) => void;

export interface SseOptions {
  /** Called for every named event (and "message"). */
  onEvent: (
    type: ExecutionEventType,
    data: ExecutionEventContract,
    lastEventId: string,
  ) => void;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
  /** Stop reconnecting when this returns true (e.g. terminal event received). */
  shouldStop?: () => boolean;
  /** Initial after-seq for first connection (?after=). */
  after?: number;
}

const TERMINAL = new Set<ExecutionEventType>([
  "workflow_finished",
  "workflow_failed",
  "workflow_cancelled",
]);

const EVENT_TYPES: ExecutionEventType[] = [
  "workflow_started",
  "node_started",
  "node_streaming",
  "node_finished",
  "node_failed",
  "edge_taken",
  "workflow_interrupted",
  "workflow_finished",
  "workflow_failed",
  "workflow_cancelled",
];

export class ResilientSSE {
  private url: string;
  private opts: SseOptions;
  private es: EventSource | null = null;
  private lastEventId = "0";
  private closed = false;
  private attempt = 0;
  private reconnectTimer: number | null = null;
  private stopped = false;

  constructor(url: string, opts: SseOptions) {
    this.url = url;
    this.opts = opts;
    if (opts.after && opts.after > 0) {
      this.lastEventId = String(opts.after);
    }
  }

  start(): void {
    this.closed = false;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.closed = true;
    this.stopped = true;
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.es?.close();
    this.es = null;
  }

  private connect(): void {
    if (this.closed || this.stopped) return;

    const sep = this.url.includes("?") ? "&" : "?";
    const after = Number(this.lastEventId) || 0;
    const fullUrl = after > 0 ? `${this.url}${sep}after=${after}` : this.url;

    const es = new EventSource(fullUrl);
    this.es = es;

    es.onopen = () => {
      this.attempt = 0;
      this.opts.onOpen?.();
    };

    const forward = (type: ExecutionEventType) => (ev: MessageEvent) => {
      if (ev.lastEventId) this.lastEventId = ev.lastEventId;
      let data: ExecutionEventContract;
      try {
        data = JSON.parse(ev.data) as ExecutionEventContract;
      } catch (error) {
        this.opts.onError?.(error);
        return;
      }
      this.opts.onEvent(type, data, this.lastEventId);
      if (TERMINAL.has(type)) {
        this.stopped = true;
        this.stop();
      }
      if (this.opts.shouldStop?.()) {
        this.stopped = true;
        this.stop();
      }
    };

    // Named events we care about
    for (const t of EVENT_TYPES) {
      es.addEventListener(t, forward(t) as EventListener);
    }

    es.onerror = (err) => {
      this.opts.onError?.(err);
      es.close();
      this.es = null;
      if (this.closed || this.stopped) return;
      // Exponential backoff 1s → 30s
      this.attempt += 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(this.attempt - 1, 5));
      this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
    };
  }
}

/** Per-execution event handler for {@link MultiplexSSE}. */
export type MultiplexHandler = (
  executionId: string,
  type: ExecutionEventType,
  data: ExecutionEventContract,
) => void;

export interface MultiplexOptions {
  onEvent: MultiplexHandler;
  onError?: (err: unknown) => void;
  onOpen?: () => void;
}

const MULTI_PATH = "/api/executions/events/multi";

/**
 * Subscribe to several executions' event streams over ONE EventSource
 * (C6-3 multiplex). The backend tags each SSE message with the owning
 * execution id inside the JSON payload and uses a composite id of
 * `{execution_id}:{seq}`, so reconnect cursors are tracked per execution and
 * echoed back via `?after=id:seq,...`. The connection stays open until every
 * requested execution has reached a terminal state (the server closes only
 * the finished execution's channel); a terminal event also stops the local
 * handler for that execution.
 */
export class MultiplexSSE {
  private ids: string[];
  private opts: MultiplexOptions;
  private es: EventSource | null = null;
  private lastSeq = new Map<string, number>();
  private terminalSeen = new Set<string>();
  private closed = false;
  private attempt = 0;
  private reconnectTimer: number | null = null;
  private stopped = false;

  constructor(ids: string[], opts: MultiplexOptions) {
    this.ids = [...new Set(ids)];
    this.opts = opts;
  }

  start(): void {
    this.closed = false;
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.closed = true;
    this.stopped = true;
    if (this.reconnectTimer != null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.es?.close();
    this.es = null;
  }

  private connect(): void {
    if (this.closed || this.stopped) return;

    const params = new URLSearchParams({ ids: this.ids.join(",") });
    if (this.lastSeq.size > 0) {
      const cursor = [...this.lastSeq.entries()]
        .map(([id, seq]) => `${id}:${seq}`)
        .join(",");
      params.set("after", cursor);
    }
    const es = new EventSource(`${MULTI_PATH}?${params.toString()}`);
    this.es = es;

    es.onopen = () => {
      this.attempt = 0;
      this.opts.onOpen?.();
    };

    const forward = (type: ExecutionEventType) => (ev: MessageEvent) => {
      let data: ExecutionEventContract;
      try {
        data = JSON.parse(ev.data) as ExecutionEventContract;
      } catch (error) {
        this.opts.onError?.(error);
        return;
      }
      const executionId = data.execution_id;
      if (!executionId || !this.ids.includes(executionId)) return;
      if (typeof data.seq === "number" && data.seq > (this.lastSeq.get(executionId) ?? 0)) {
        this.lastSeq.set(executionId, data.seq);
      }
      this.opts.onEvent(executionId, type, data);
      if (TERMINAL.has(type)) {
        this.terminalSeen.add(executionId);
        if (this.terminalSeen.size >= this.ids.length) {
          this.stopped = true;
          this.stop();
        }
      }
    };

    for (const t of EVENT_TYPES) {
      es.addEventListener(t, forward(t) as EventListener);
    }

    es.onerror = (err) => {
      this.opts.onError?.(err);
      es.close();
      this.es = null;
      if (this.closed || this.stopped) return;
      this.attempt += 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(this.attempt - 1, 5));
      this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
    };
  }
}
