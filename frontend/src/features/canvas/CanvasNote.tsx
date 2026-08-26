import { useEffect, useRef, useState } from "react";
import { StickyNote, Trash2 } from "lucide-react";
import { useReactFlow } from "@xyflow/react";

import type { CanvasNote as CanvasNoteModel } from "@/types/dsl";

interface Props {
  note: CanvasNoteModel;
  onMove: (position: { x: number; y: number }) => void;
  onDragStart: () => void;
  onDragStop: () => void;
  onTextChange: (text: string) => void;
  onRemove: () => void;
}

export function CanvasNote({ note, onMove, onDragStart, onDragStop, onTextChange, onRemove }: Props) {
  const { screenToFlowPosition } = useReactFlow();
  const [text, setText] = useState(note.text);
  const dragRef = useRef<{ origin: { x: number; y: number }; start: { x: number; y: number } } | null>(null);

  useEffect(() => setText(note.text), [note.text]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest("button, textarea")) return;
    event.preventDefault();
    event.stopPropagation();
    dragRef.current = {
      origin: note.position,
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
      data-testid={`canvas-note-${note.id}`}
      className="pointer-events-auto absolute overflow-hidden rounded-md border bg-amber-400/10 shadow-card"
      style={{ left: note.position.x, top: note.position.y, width: note.width, height: note.height, borderColor: note.color, zIndex: 20 }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <div className="flex h-8 items-center gap-2 border-b px-2.5" style={{ borderColor: `${note.color}80` }}>
        <StickyNote size={12} style={{ color: note.color }} />
        <span className="flex-1 font-mono text-[9px] uppercase tracking-[0.16em] text-ghost">NOTE</span>
        <button
          type="button"
          aria-label="删除便签"
          title="删除便签"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={onRemove}
          className="flex h-6 w-6 items-center justify-center rounded text-ghost hover:bg-bad/10 hover:text-bad"
        >
          <Trash2 size={12} />
        </button>
      </div>
      <textarea
        aria-label="便签内容"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onBlur={() => onTextChange(text)}
        onPointerDown={(event) => event.stopPropagation()}
        className="nodrag h-[calc(100%-2rem)] w-full resize-none bg-transparent px-2.5 py-2 font-mono text-[10px] leading-5 text-ice outline-hidden placeholder:text-ghost/50"
        placeholder="记录流程意图…"
      />
    </div>
  );
}
