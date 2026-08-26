import { FolderPlus, StickyNote } from "lucide-react";

interface Props {
  editingAllowed: boolean;
  selectionCount: number;
  onAddGroup: () => void;
  onAddNote: () => void;
}

export function CanvasObjectActions({ editingAllowed, selectionCount, onAddGroup, onAddNote }: Props) {
  return (
    <div role="group" aria-label="画布对象" className="flex shrink-0 items-center gap-1 rounded-md border border-line bg-ink/80 p-0.5">
      <button
        type="button"
        aria-label="添加分组"
        title={selectionCount ? `将 ${selectionCount} 个节点加入分组` : "添加分组"}
        disabled={!editingAllowed}
        onClick={onAddGroup}
        className="flex h-7 w-7 items-center justify-center rounded text-ghost transition hover:bg-line hover:text-ice disabled:opacity-35"
      >
        <FolderPlus size={13} />
      </button>
      <button
        type="button"
        aria-label="添加便签"
        title="添加便签"
        disabled={!editingAllowed}
        onClick={onAddNote}
        className="flex h-7 w-7 items-center justify-center rounded text-ghost transition hover:bg-line hover:text-ice disabled:opacity-35"
      >
        <StickyNote size={13} />
      </button>
    </div>
  );
}
