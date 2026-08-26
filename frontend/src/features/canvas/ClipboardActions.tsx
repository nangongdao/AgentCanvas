import { Clipboard, ClipboardPaste } from "lucide-react";

interface Props {
  editingAllowed: boolean;
  selectionCount: number;
  canPaste: boolean;
  onCopy: () => void;
  onPaste: () => void;
}

/** Copy/paste subgraph controls. Backed by Ctrl/Cmd+C and Ctrl/Cmd+V in the
 * canvas; these buttons expose the same actions for pointer and AT users. */
export function ClipboardActions({
  editingAllowed,
  selectionCount,
  canPaste,
  onCopy,
  onPaste,
}: Props) {
  return (
    <div
      role="group"
      aria-label="复制粘贴"
      className="flex shrink-0 items-center gap-1 rounded-md border border-line bg-ink/80 p-0.5"
    >
      <button
        type="button"
        aria-label="复制"
        aria-keyshortcuts="Control+C Meta+C"
        title={selectionCount ? `复制 ${selectionCount} 个节点 (Ctrl+C)` : "复制 (Ctrl+C)"}
        disabled={!editingAllowed || selectionCount === 0}
        onClick={onCopy}
        className="flex h-7 w-7 items-center justify-center rounded text-ghost transition hover:bg-line hover:text-ice disabled:opacity-35"
      >
        <Clipboard size={13} />
      </button>
      <button
        type="button"
        aria-label="粘贴"
        aria-keyshortcuts="Control+V Meta+V"
        title="粘贴 (Ctrl+V)"
        disabled={!editingAllowed || !canPaste}
        onClick={onPaste}
        className="flex h-7 w-7 items-center justify-center rounded text-ghost transition hover:bg-line hover:text-ice disabled:opacity-35"
      >
        <ClipboardPaste size={13} />
      </button>
    </div>
  );
}
