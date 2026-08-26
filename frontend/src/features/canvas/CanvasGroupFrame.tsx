import { useRef } from "react";
import { ChevronDown, ChevronUp, FolderKanban, Trash2 } from "lucide-react";
import { useReactFlow } from "@xyflow/react";

import type { CanvasGroup } from "@/types/dsl";

interface Props {
  group: CanvasGroup;
  onMove: (position: { x: number; y: number }) => void;
  onDragStart: () => void;
  onDragStop: () => void;
  onToggle: () => void;
  onRename: (name: string) => void;
  onRemove: () => void;
}

export function CanvasGroupFrame({
  group,
  onMove,
  onDragStart,
  onDragStop,
  onToggle,
  onRename,
  onRemove,
}: Props) {
  const { screenToFlowPosition } = useReactFlow();
  const dragRef = useRef<{
    origin: { x: number; y: number };
    start: { x: number; y: number };
  } | null>(null);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button, input")) return;
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = {
      origin: group.position,
      start: screenToFlowPosition({ x: event.clientX, y: event.clientY }),
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    onDragStart();
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current;
    if (!drag) return;
    const current = screenToFlowPosition({ x: event.clientX, y: event.clientY });
    onMove({
      x: Math.round(drag.origin.x + current.x - drag.start.x),
      y: Math.round(drag.origin.y + current.y - drag.start.y),
    });
  };

  const handlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
    onDragStop();
  };

  return (
    <div
      data-testid={`canvas-group-${group.id}`}
      className="pointer-events-none absolute rounded-lg border border-dashed bg-void/25 shadow-inner"
      style={{
        left: group.position.x,
        top: group.position.y,
        width: group.width,
        height: group.collapsed ? 48 : group.height,
        borderColor: group.color,
        zIndex: 0,
      }}
    >
      <div
        className="pointer-events-auto flex h-11 cursor-grab items-center gap-2 border-b border-dashed px-2.5 active:cursor-grabbing"
        style={{ borderColor: `${group.color}80` }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        <FolderKanban size={13} style={{ color: group.color }} />
        <input
          aria-label={`分组名称 ${group.id}`}
          value={group.name}
          onChange={(event) => onRename(event.target.value)}
          onPointerDown={(event) => event.stopPropagation()}
          className="nodrag min-w-0 flex-1 bg-transparent font-mono text-[10px] font-semibold text-ice outline-hidden"
        />
        <span className="font-mono text-[8px] text-ghost/55">{group.node_ids.length}</span>
        <button
          type="button"
          aria-label={group.collapsed ? `展开分组 ${group.name}` : `折叠分组 ${group.name}`}
          title={group.collapsed ? "展开分组" : "折叠分组"}
          onPointerDown={(event) => event.stopPropagation()}
          onClick={onToggle}
          className="flex h-6 w-6 items-center justify-center rounded text-ghost hover:bg-line hover:text-ice"
        >
          {group.collapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
        <button
          type="button"
          aria-label={`删除分组 ${group.name}`}
          title="删除分组"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={onRemove}
          className="flex h-6 w-6 items-center justify-center rounded text-ghost hover:bg-bad/10 hover:text-bad"
        >
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  );
}
