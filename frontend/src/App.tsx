import { lazy, Profiler, Suspense, type ProfilerOnRenderCallback } from "react";

const FlowCanvas = lazy(() =>
  import("@/features/canvas/FlowCanvas").then((module) => ({ default: module.FlowCanvas })),
);

declare global {
  interface Window {
    __AGENTCANVAS_PERF__?: { commits: number[] };
  }
}

const recordCanvasCommit: ProfilerOnRenderCallback = (
  _id,
  _phase,
  actualDuration,
) => {
  if (!new URLSearchParams(window.location.search).has("perf")) return;
  const metrics = (window.__AGENTCANVAS_PERF__ ??= { commits: [] });
  metrics.commits.push(actualDuration);
  if (metrics.commits.length > 500) metrics.commits.shift();
};

export default function App() {
  return (
    <main className="h-full w-full">
      <Profiler id="workflow-canvas" onRender={recordCanvasCommit}>
        <Suspense
          fallback={
            <div className="flex h-full w-full items-center justify-center bg-void text-ghost">
              <span className="animate-pulse text-xs">加载画布…</span>
            </div>
          }
        >
          <FlowCanvas />
        </Suspense>
      </Profiler>
    </main>
  );
}
