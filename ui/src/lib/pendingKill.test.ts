import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  adoptAuthoritativePendingKill,
  clearPendingKill,
  pendingKillStorageKey,
  persistPendingKill,
  readPendingKill,
  subscribePendingKill,
} from "./pendingKill";

const COMMAND_ID = "10000000-0000-4000-8000-000000000001";

beforeEach(() => window.localStorage.clear());

describe("pending kill identity", () => {
  it("round-trips only a canonical UUID and clears the matching command", () => {
    expect(persistPendingKill(COMMAND_ID.toUpperCase())).toBe(true);
    expect(readPendingKill()).toEqual({ kind: "pending", commandId: COMMAND_ID });
    expect(clearPendingKill("20000000-0000-4000-8000-000000000002")).toBe(false);
    expect(readPendingKill()).toEqual({ kind: "pending", commandId: COMMAND_ID });
    expect(persistPendingKill("20000000-0000-4000-8000-000000000002")).toBe(false);
    expect(clearPendingKill(COMMAND_ID)).toBe(true);
    expect(readPendingKill()).toEqual({ kind: "clear" });
  });

  it("lets server authority replace a stale canonical reservation without accepting malformed state", () => {
    const authoritative = "20000000-0000-4000-8000-000000000002";
    expect(persistPendingKill(COMMAND_ID)).toBe(true);

    expect(adoptAuthoritativePendingKill(authoritative)).toBe(true);
    expect(readPendingKill()).toEqual({ kind: "pending", commandId: authoritative });

    window.localStorage.setItem(pendingKillStorageKey, "hostile");
    expect(adoptAuthoritativePendingKill(COMMAND_ID)).toBe(false);
    expect(window.localStorage.getItem(pendingKillStorageKey)).toBe("hostile");
  });

  it("fails closed for malformed or unavailable durable storage", () => {
    expect(persistPendingKill("not-a-command")).toBe(false);
    window.localStorage.setItem(pendingKillStorageKey, "hostile");
    expect(readPendingKill()).toEqual({ kind: "unavailable" });
    expect(window.localStorage.getItem(pendingKillStorageKey)).toBe("hostile");
    window.localStorage.clear();
    const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    expect(persistPendingKill(COMMAND_ID)).toBe(false);
    expect(readPendingKill()).toEqual({ kind: "unavailable" });
    storage.mockRestore();
  });

  it("synchronizes a reservation and release delivered by another tab", () => {
    const observed = vi.fn();
    const unsubscribe = subscribePendingKill(observed);

    window.localStorage.setItem(pendingKillStorageKey, COMMAND_ID);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: pendingKillStorageKey,
        newValue: COMMAND_ID,
        storageArea: window.localStorage,
      }),
    );
    expect(observed).toHaveBeenLastCalledWith({ kind: "pending", commandId: COMMAND_ID });

    window.localStorage.removeItem(pendingKillStorageKey);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: pendingKillStorageKey,
        newValue: null,
        storageArea: window.localStorage,
      }),
    );
    expect(observed).toHaveBeenLastCalledWith({ kind: "clear" });
    unsubscribe();
  });
});
