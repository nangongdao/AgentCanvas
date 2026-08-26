import { useEffect, useRef } from "react";

import { ResilientSSE } from "@/api/sse";
import { useExecutionStore } from "@/stores/executionStore";

/** Subscribe to execution SSE and feed the execution store. */
export function useExecutionSSE(executionId: string | null): void {
  const handleEvent = useExecutionStore((s) => s.handleEvent);
  const clientRef = useRef<ResilientSSE | null>(null);

  useEffect(() => {
    if (!executionId) return;

    const client = new ResilientSSE(`/api/executions/${executionId}/events`, {
      onEvent: (type, data) => handleEvent(type, data),
      onError: (err) => {
        // Soft log; reconnect is automatic
        // eslint-disable-next-line no-console
        console.warn("[SSE]", err);
      },
    });
    clientRef.current = client;
    client.start();

    return () => {
      client.stop();
      clientRef.current = null;
    };
  }, [executionId, handleEvent]);
}
