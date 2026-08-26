import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

interface DialogFocusOptions {
  open: boolean;
  onClose?: () => void;
  escapeEnabled?: boolean;
  initialFocusRef?: RefObject<HTMLElement | null>;
}

function focusableElements(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) =>
      element.getClientRects().length > 0 &&
      element.getAttribute("aria-hidden") !== "true" &&
      !element.hasAttribute("inert"),
  );
}

export function useDialogFocus<T extends HTMLElement>({
  open,
  onClose,
  escapeEnabled = true,
  initialFocusRef,
}: DialogFocusOptions): RefObject<T | null> {
  const dialogRef = useRef<T>(null);
  const closeRef = useRef(onClose);
  const escapeEnabledRef = useRef(escapeEnabled);
  closeRef.current = onClose;
  escapeEnabledRef.current = escapeEnabled;

  useEffect(() => {
    if (!open) return;
    const returnFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const frame = window.requestAnimationFrame(() => {
      const root = dialogRef.current;
      const preferred =
        initialFocusRef?.current ??
        root?.querySelector<HTMLElement>("[data-dialog-initial-focus]") ??
        (root ? focusableElements(root)[0] : null) ??
        root;
      preferred?.focus({ preventScroll: true });
    });

    const onKeyDown = (event: KeyboardEvent) => {
      const root = dialogRef.current;
      if (!root) return;
      if (event.key === "Escape" && escapeEnabledRef.current && closeRef.current) {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusableElements(root);
      if (elements.length === 0) {
        event.preventDefault();
        root.focus({ preventScroll: true });
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !root.contains(active))) {
        event.preventDefault();
        last.focus({ preventScroll: true });
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus({ preventScroll: true });
      }
    };

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown, true);
      if (returnFocus?.isConnected) {
        window.requestAnimationFrame(() => returnFocus.focus({ preventScroll: true }));
      }
    };
  }, [initialFocusRef, open]);

  return dialogRef;
}
