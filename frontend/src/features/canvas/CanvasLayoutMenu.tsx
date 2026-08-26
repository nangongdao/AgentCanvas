import { useEffect, useRef, useState } from "react";
import {
  AlignHorizontalJustifyCenter,
  AlignHorizontalJustifyEnd,
  AlignHorizontalJustifyStart,
  AlignVerticalJustifyCenter,
  AlignVerticalJustifyEnd,
  AlignVerticalJustifyStart,
  ArrowLeftRight,
  ArrowUpDown,
  ChevronDown,
  Workflow,
} from "lucide-react";

import type {
  AlignmentMode,
  DistributionMode,
} from "@/features/canvas/canvasLayout";

interface Props {
  editingAllowed: boolean;
  selectionCount: number;
  onAutoLayout: () => void;
  onAlign: (mode: AlignmentMode) => void;
  onDistribute: (mode: DistributionMode) => void;
}

export function CanvasLayoutMenu({
  editingAllowed,
  selectionCount,
  onAutoLayout,
  onAlign,
  onDistribute,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuItemsRef = useRef<Array<HTMLButtonElement | null>>([]);
  const canAlign = editingAllowed && selectionCount >= 2;
  const canDistribute = editingAllowed && selectionCount >= 3;

  useEffect(() => {
    if (!open) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
        setOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    window.requestAnimationFrame(() => menuItemsRef.current[0]?.focus());
  }, [open]);

  const command = (action: () => void) => {
    action();
    setOpen(false);
    triggerRef.current?.focus();
  };

  const handleMenuKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const items = menuItemsRef.current.filter(
      (item): item is HTMLButtonElement => item !== null && !item.disabled,
    );
    const current = document.activeElement;
    const index = items.indexOf(current as HTMLButtonElement);
    if (items.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      items[(index + delta + items.length) % items.length]?.focus();
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      items[event.key === "Home" ? 0 : items.length - 1]?.focus();
    }
  };

  const itemClass =
    "flex h-8 w-full items-center gap-2 rounded px-2 text-left text-xs text-ice transition hover:bg-line/60 focus-visible:outline-2 focus-visible:outline-pulse disabled:cursor-not-allowed disabled:opacity-35";

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        aria-label="布局与对齐"
        aria-expanded={open}
        aria-haspopup="menu"
        disabled={!editingAllowed}
        onClick={() => setOpen((value) => !value)}
        className="flex h-8 items-center gap-1.5 rounded-md border border-line bg-ink/80 px-2 text-xs text-ice transition hover:border-ghost/50 hover:bg-line/60 focus-visible:outline-2 focus-visible:outline-pulse disabled:cursor-not-allowed disabled:opacity-35"
        title={editingAllowed ? `布局与对齐（已选 ${selectionCount} 个节点）` : "需要先取得工作流编辑权"}
      >
        <Workflow size={14} />
        <span className="hidden md:inline">布局</span>
        <ChevronDown size={12} className={open ? "rotate-180 transition" : "transition"} />
      </button>

      {open && (
        <div
            role="menu"
            aria-label="布局与对齐操作"
            onKeyDown={handleMenuKeyDown}
            className="absolute right-0 top-10 z-50 w-52 rounded-md border border-line bg-ink/95 p-1.5 shadow-card backdrop-blur-md"
          >
            <button
              ref={(element) => { menuItemsRef.current[0] = element; }}
            type="button"
            role="menuitem"
            onClick={() => command(onAutoLayout)}
            className={itemClass}
          >
            <Workflow size={14} />
            <span>自动布局</span>
          </button>
          <div className="my-1 h-px bg-line" />
          <p className="px-2 py-1 font-mono text-[9px] uppercase tracking-[0.12em] text-ghost/60">
            对齐 · 已选 {selectionCount}
          </p>
          <button ref={(element) => { menuItemsRef.current[1] = element; }} type="button" role="menuitem" disabled={!canAlign} onClick={() => command(() => onAlign("left"))} className={itemClass}>
            <AlignHorizontalJustifyStart size={14} />
            <span>左对齐</span>
          </button>
          <button ref={(element) => { menuItemsRef.current[2] = element; }} type="button" role="menuitem" disabled={!canAlign} onClick={() => command(() => onAlign("center-x"))} className={itemClass}>
            <AlignHorizontalJustifyCenter size={14} />
            <span>水平居中</span>
          </button>
          <button ref={(element) => { menuItemsRef.current[3] = element; }} type="button" role="menuitem" disabled={!canAlign} onClick={() => command(() => onAlign("right"))} className={itemClass}>
            <AlignHorizontalJustifyEnd size={14} />
            <span>右对齐</span>
          </button>
          <button ref={(element) => { menuItemsRef.current[4] = element; }} type="button" role="menuitem" disabled={!canAlign} onClick={() => command(() => onAlign("top"))} className={itemClass}>
            <AlignVerticalJustifyStart size={14} />
            <span>顶端对齐</span>
          </button>
          <button ref={(element) => { menuItemsRef.current[5] = element; }} type="button" role="menuitem" disabled={!canAlign} onClick={() => command(() => onAlign("center-y"))} className={itemClass}>
            <AlignVerticalJustifyCenter size={14} />
            <span>垂直居中</span>
          </button>
          <button ref={(element) => { menuItemsRef.current[6] = element; }} type="button" role="menuitem" disabled={!canAlign} onClick={() => command(() => onAlign("bottom"))} className={itemClass}>
            <AlignVerticalJustifyEnd size={14} />
            <span>底端对齐</span>
          </button>
          <div className="my-1 h-px bg-line" />
          <p className="px-2 py-1 font-mono text-[9px] uppercase tracking-[0.12em] text-ghost/60">
            等距
          </p>
          <button ref={(element) => { menuItemsRef.current[7] = element; }} type="button" role="menuitem" disabled={!canDistribute} onClick={() => command(() => onDistribute("horizontal"))} className={itemClass}>
            <ArrowLeftRight size={14} />
            <span>水平等距</span>
          </button>
          <button ref={(element) => { menuItemsRef.current[8] = element; }} type="button" role="menuitem" disabled={!canDistribute} onClick={() => command(() => onDistribute("vertical"))} className={itemClass}>
            <ArrowUpDown size={14} />
            <span>垂直等距</span>
          </button>
        </div>
      )}
    </div>
  );
}
