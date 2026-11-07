const STORAGE_KEY = "hmt-crypto.pending-kill-command.v1";
const PROBE_KEY = "hmt-crypto.pending-kill-storage-probe.v1";
const LOCAL_EVENT = "hmt-crypto:pending-kill-changed";
const CHANNEL_NAME = "hmt-crypto.pending-kill.v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export type PendingKillState =
  | { readonly kind: "clear" }
  | { readonly kind: "pending"; readonly commandId: string }
  | { readonly kind: "unavailable" };

type Listener = (state: PendingKillState) => void;

let broadcastChannel: BroadcastChannel | null | undefined;

function channel(): BroadcastChannel | null {
  if (broadcastChannel !== undefined) return broadcastChannel;
  if (typeof BroadcastChannel === "undefined") {
    broadcastChannel = null;
    return null;
  }
  try {
    broadcastChannel = new BroadcastChannel(CHANNEL_NAME);
  } catch {
    broadcastChannel = null;
  }
  return broadcastChannel;
}

function probeStorage(): void {
  const previous = window.localStorage.getItem(PROBE_KEY);
  window.localStorage.setItem(PROBE_KEY, "ok");
  if (window.localStorage.getItem(PROBE_KEY) !== "ok") throw new Error("storage verification failed");
  if (previous === null) window.localStorage.removeItem(PROBE_KEY);
  else window.localStorage.setItem(PROBE_KEY, previous);
}

function announceChange(): void {
  window.dispatchEvent(new Event(LOCAL_EVENT));
  try {
    channel()?.postMessage("changed");
  } catch {
    // localStorage storage events remain the cross-tab fallback.
  }
}

/** Read and verify the cross-tab command reservation. Any unreadable state fails closed. */
export function readPendingKill(): PendingKillState {
  try {
    probeStorage();
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (value === null) return { kind: "clear" };
    if (!UUID.test(value)) return { kind: "unavailable" };
    return { kind: "pending", commandId: value.toLowerCase() };
  } catch {
    return { kind: "unavailable" };
  }
}

export function persistPendingKill(commandId: string): boolean {
  if (!UUID.test(commandId)) return false;
  const normalized = commandId.toLowerCase();
  try {
    const previous = window.localStorage.getItem(STORAGE_KEY);
    if (previous !== null && previous !== normalized) return false;
    window.localStorage.setItem(STORAGE_KEY, normalized);
    if (window.localStorage.getItem(STORAGE_KEY) !== normalized) return false;
    if (previous !== normalized) announceChange();
    return true;
  } catch {
    return false;
  }
}

/** Replace a stale canonical reservation only after an authenticated server observation. */
export function adoptAuthoritativePendingKill(commandId: string): boolean {
  if (!UUID.test(commandId)) return false;
  const normalized = commandId.toLowerCase();
  try {
    const previous = window.localStorage.getItem(STORAGE_KEY);
    if (previous !== null && !UUID.test(previous)) return false;
    window.localStorage.setItem(STORAGE_KEY, normalized);
    if (window.localStorage.getItem(STORAGE_KEY) !== normalized) return false;
    if (previous?.toLowerCase() !== normalized) announceChange();
    return true;
  } catch {
    return false;
  }
}

export function clearPendingKill(commandId: string): boolean {
  try {
    const normalized = commandId.toLowerCase();
    const existing = window.localStorage.getItem(STORAGE_KEY);
    if (existing === null) return true;
    if (existing !== normalized) return false;
    window.localStorage.removeItem(STORAGE_KEY);
    if (window.localStorage.getItem(STORAGE_KEY) !== null) return false;
    announceChange();
    return true;
  } catch {
    return false;
  }
}

/** Subscribe to same-document, storage-event, and BroadcastChannel reservations. */
export function subscribePendingKill(listener: Listener): () => void {
  const publishCurrent = () => listener(readPendingKill());
  const onStorage = (event: StorageEvent) => {
    if (event.storageArea === window.localStorage && event.key === STORAGE_KEY) publishCurrent();
  };
  const onLocal = () => publishCurrent();
  const activeChannel = channel();
  const onBroadcast = () => publishCurrent();

  window.addEventListener("storage", onStorage);
  window.addEventListener(LOCAL_EVENT, onLocal);
  activeChannel?.addEventListener("message", onBroadcast);
  return () => {
    window.removeEventListener("storage", onStorage);
    window.removeEventListener(LOCAL_EVENT, onLocal);
    activeChannel?.removeEventListener("message", onBroadcast);
  };
}

export const pendingKillStorageKey = STORAGE_KEY;
