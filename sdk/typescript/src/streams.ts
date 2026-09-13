/**
 * Server-Sent Events (SSE) stream handler for real-time execution events.
 */

import type { ExecutionEvent } from './types';

/**
 * Callback function for processing execution events.
 */
export type EventCallback = (event: ExecutionEvent) => void;

/**
 * Options for SSE stream connection.
 */
export interface StreamOptions {
  onEvent: EventCallback;
  onError?: (error: Error) => void;
  onEnd?: () => void;
}

/**
 * Parse SSE data field into ExecutionEvent.
 */
function parseEvent(data: string): ExecutionEvent | null {
  try {
    return JSON.parse(data) as ExecutionEvent;
  } catch {
    return null;
  }
}

/**
 * Create and manage an SSE connection for execution events.
 */
export class ExecutionStream {
  private controller: AbortController;
  private url: string;
  private headers: Record<string, string>;
  private options: StreamOptions;

  constructor(url: string, headers: Record<string, string>, options: StreamOptions) {
    this.url = url;
    this.headers = headers;
    this.options = options;
    this.controller = new AbortController();
  }

  /**
   * Start streaming execution events.
   */
  async start(): Promise<void> {
    try {
      const response = await fetch(this.url, {
        headers: this.headers,
        signal: this.controller.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      if (!response.body) {
        throw new Error('Response body is null');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          this.options.onEnd?.();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            const event = parseEvent(data);
            if (event) {
              this.options.onEvent(event);
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        this.options.onError?.(error);
      }
    }
  }

  /**
   * Stop the stream and close the connection.
   */
  stop(): void {
    this.controller.abort();
  }
}

/**
 * Create an execution stream for real-time event monitoring.
 */
export function createExecutionStream(
  executionId: string,
  baseURL: string,
  apiKey: string,
  options: StreamOptions
): ExecutionStream {
  const url = `${baseURL}/api/executions/${executionId}/stream`;
  const headers = {
    'Authorization': `Bearer ${apiKey}`,
    'Accept': 'text/event-stream',
  };

  return new ExecutionStream(url, headers, options);
}
