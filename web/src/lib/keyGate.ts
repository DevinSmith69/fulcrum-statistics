/*
 * A full-screen layer (the highlights reel) needs its keys before every other shortcut on the page: Escape must
 * not also leave broadcast mode, Space must not also pause the demo director, R must not start a replay beneath it.
 * Capture listeners on `window` run in registration order, so this one is registered when the module loads, before
 * any component's effect, and hands the key to whoever holds the gate.
 */

/** Returns true when it handled (or deliberately swallowed) the key. */
type KeyHandler = (e: KeyboardEvent) => boolean;

let holder: KeyHandler | null = null;

if (typeof window !== "undefined") {
  window.addEventListener(
    "keydown",
    (e) => {
      if (holder?.(e)) e.stopImmediatePropagation();
    },
    true,
  );
}

/** Route every keydown to `handler` first until the returned release function is called. */
export function grabKeys(handler: KeyHandler): () => void {
  holder = handler;
  return () => {
    if (holder === handler) holder = null;
  };
}
