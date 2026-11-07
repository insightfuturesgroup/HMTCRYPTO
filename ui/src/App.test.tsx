import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { controlApi, ControlApiResponseError } from "./api/client";
import { dashboardFixture } from "./test/fixtures";
import {
  pendingKillStorageKey,
  persistPendingKill,
  readPendingKill,
} from "./lib/pendingKill";

const SESSION = {
  authenticated: true,
  csrfToken: "csrf-value",
  absoluteExpiresAt: "2099-11-06T12:00:00Z",
} as const;

beforeEach(() => {
  vi.spyOn(controlApi, "activeKill").mockResolvedValue(null);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("App safety behavior", () => {
  it("keeps the authenticated kill switch available when dashboard evidence fails", async () => {
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockRejectedValue(new TypeError("backend unavailable"));

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/dashboard evidence is unavailable/i);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
        "aria-disabled",
        "false",
      ),
    );
    const environmentBanner = screen.getByTestId("environment-banner");
    expect(environmentBanner).toHaveTextContent("UNAVAILABLE");
    expect(environmentBanner).toHaveClass("is-danger");
  });

  it("renders a loud danger treatment for LIVE even on testnet", async () => {
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(
      dashboardFixture({ mode: "LIVE", environment: "testnet" }),
    );

    render(<App />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/live execution mode/i);
    const environmentBanner = screen.getByTestId("environment-banner");
    expect(environmentBanner).toHaveTextContent("INDIA · TESTNET · LIVE");
    expect(environmentBanner).toHaveClass("is-danger");
  });

  it("provides functional view navigation instead of inert controls", async () => {
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    const user = userEvent.setup();

    render(<App />);
    await screen.findByText("100,000.00");
    await user.click(screen.getAllByRole("button", { name: /positions/i })[0]);

    expect(screen.getByRole("heading", { name: "Open positions" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Open orders" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /positions/i })[0]).toHaveAttribute(
      "aria-current",
      "page",
    );

    await user.click(screen.getAllByRole("button", { name: /^orders$/i })[0]);
    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Open orders" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "Open positions" })).not.toBeInTheDocument();
  });

  it("polls authoritative status after an ambiguous one-shot kill submission", async () => {
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    vi.spyOn(controlApi, "previewKill").mockResolvedValue({
      previewId: "9cab76c9-814c-4873-9e0c-33b52de90c85",
      expiresAt: "2099-11-06T12:00:00Z",
      flatten: false,
      openOrderCount: 1,
      positionCount: 1,
      evidenceState: "RECONCILED_CLAIM",
      evidenceObservedAt: "2026-11-06T12:00:00Z",
      warning: "Cancel risk-increasing orders",
    });
    const engage = vi
      .spyOn(controlApi, "engageKill")
      .mockRejectedValue(new TypeError("response lost"));
    const status = vi.spyOn(controlApi, "killStatus").mockImplementation((commandId) =>
      Promise.resolve({
        commandId,
        idempotencyKey: commandId,
        state: "COMPLETED",
        acceptedAt: "2026-11-06T12:00:00Z",
        authoritative: true,
      }),
    );
    const user = userEvent.setup();

    render(<App />);
    await screen.findByText("100,000.00");
    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));
    await user.click(await screen.findByRole("button", { name: "ENGAGE KILL SWITCH" }));
    await user.click(await screen.findByRole("button", { name: /close and monitor status/i }));

    expect(engage).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.getByText(/is COMPLETED/)).toBeInTheDocument(), {
      timeout: 3_000,
    });
    expect(status).toHaveBeenCalledOnce();
    expect(status.mock.calls[0]?.[0]).toBe(engage.mock.calls[0]?.[4]);
    await waitFor(() => expect(readPendingKill()).toEqual({ kind: "clear" }));
  });

  it("restores a nonterminal command identity after reload before enabling a new kill", async () => {
    const commandId = "10000000-0000-4000-8000-000000000001";
    expect(persistPendingKill(commandId)).toBe(true);
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    const status = vi.spyOn(controlApi, "killStatus").mockResolvedValue({
      commandId,
      idempotencyKey: commandId,
      state: "COMPLETED",
      acceptedAt: "2026-11-06T12:00:00Z",
      authoritative: true,
    });

    render(<App />);
    await screen.findByText("100,000.00");

    expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByText(new RegExp(commandId))).toBeInTheDocument();
    await waitFor(() => expect(status).toHaveBeenCalledWith(commandId, expect.any(AbortSignal)), {
      timeout: 3_000,
    });
    await waitFor(() => expect(readPendingKill()).toEqual({ kind: "clear" }));
    expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
      "aria-disabled",
      "false",
    );
  });

  it("discovers and blocks on a server-authoritative active kill without local state", async () => {
    const commandId = "39b7f399-4e00-47da-8e80-7fd4c813f94a";
    vi.mocked(controlApi.activeKill).mockResolvedValue({
      commandId,
      idempotencyKey: commandId,
      state: "CLAIMED",
      acceptedAt: "2026-11-06T12:00:00Z",
      authoritative: true,
    });
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    vi.spyOn(controlApi, "killStatus").mockResolvedValue({
      commandId,
      idempotencyKey: commandId,
      state: "CLAIMED",
      acceptedAt: "2026-11-06T12:00:00Z",
      authoritative: true,
    });

    render(<App />);

    expect(await screen.findByText(new RegExp(commandId))).toBeInTheDocument();
    expect(controlApi.activeKill).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(readPendingKill()).toEqual({ kind: "pending", commandId });
  });

  it("resumes capped status reconciliation after focus proves the active endpoint is reachable", async () => {
    vi.useFakeTimers();
    const commandId = "10000000-0000-4000-8000-000000000001";
    expect(persistPendingKill(commandId)).toBe(true);
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    const status = vi
      .spyOn(controlApi, "killStatus")
      .mockRejectedValueOnce(new TypeError("status unavailable"))
      .mockRejectedValueOnce(new TypeError("status unavailable"))
      .mockRejectedValueOnce(new TypeError("status unavailable"))
      .mockRejectedValueOnce(new TypeError("status unavailable"))
      .mockRejectedValueOnce(new TypeError("status unavailable"))
      .mockResolvedValue({
        commandId,
        idempotencyKey: commandId,
        state: "COMPLETED",
        acceptedAt: "2026-11-06T12:00:00Z",
        authoritative: true,
      });

    render(<App />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    for (let attempt = 0; attempt < 5; attempt += 1) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2_001);
      });
    }
    expect(status).toHaveBeenCalledTimes(5);
    expect(screen.getByRole("button", { name: /resume status reconciliation/i })).toBeEnabled();

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });

    expect(status).toHaveBeenCalledTimes(6);
    expect(readPendingKill()).toEqual({ kind: "clear" });
  });

  it("adopts a different server-authoritative active identity and clears it at terminal status", async () => {
    vi.useFakeTimers();
    const staleId = "10000000-0000-4000-8000-000000000001";
    const activeId = "20000000-0000-4000-8000-000000000002";
    expect(persistPendingKill(staleId)).toBe(true);
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    vi.mocked(controlApi.activeKill)
      .mockResolvedValueOnce({
        commandId: activeId,
        idempotencyKey: activeId,
        state: "CLAIMED",
        acceptedAt: "2026-11-06T12:00:00Z",
        authoritative: true,
      })
      .mockResolvedValue(null);
    vi.spyOn(controlApi, "killStatus").mockResolvedValue({
      commandId: activeId,
      idempotencyKey: activeId,
      state: "COMPLETED",
      acceptedAt: "2026-11-06T12:00:00Z",
      authoritative: true,
    });

    render(<App />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(readPendingKill()).toEqual({ kind: "pending", commandId: activeId });

    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(controlApi.killStatus).toHaveBeenCalledWith(activeId, expect.any(AbortSignal));
    expect(readPendingKill()).toEqual({ kind: "clear" });
    expect(screen.queryByText(/could not be reserved across tabs/i)).not.toBeInTheDocument();
  });

  it("clears a losing lost-response identity only after active absence and exact 404 agree", async () => {
    vi.useFakeTimers();
    const winnerId = "10000000-0000-4000-8000-000000000001";
    const losingId = "20000000-0000-4000-8000-000000000002";
    expect(persistPendingKill(losingId)).toBe(true);
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    const terminalServerCommands = new Map([
      [
        winnerId,
        {
          commandId: winnerId,
          idempotencyKey: winnerId,
          state: "COMPLETED" as const,
          acceptedAt: "2026-11-06T12:00:00Z",
          authoritative: true,
        },
      ],
    ]);
    vi.mocked(controlApi.activeKill).mockImplementation(() => {
      const active = [...terminalServerCommands.values()].find(
        (item) => item.state !== ("COMPLETED" as string) && item.state !== ("FAILED" as string),
      );
      return Promise.resolve(active ?? null);
    });
    const status = vi.spyOn(controlApi, "killStatus").mockImplementation((commandId) => {
      const command = terminalServerCommands.get(commandId);
      return command === undefined
        ? Promise.reject(new ControlApiResponseError(404))
        : Promise.resolve(command);
    });

    render(<App />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(readPendingKill()).toEqual({ kind: "pending", commandId: losingId });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });
    await act(async () => {
      await Promise.resolve();
    });

    expect(status).toHaveBeenCalledWith(losingId, expect.any(AbortSignal));
    expect(readPendingKill()).toEqual({ kind: "clear" });
  });

  it("keeps a losing identity reserved when exact status fails with transient 503", async () => {
    vi.useFakeTimers();
    const losingId = "20000000-0000-4000-8000-000000000002";
    expect(persistPendingKill(losingId)).toBe(true);
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());
    vi.mocked(controlApi.activeKill).mockResolvedValue(null);
    vi.spyOn(controlApi, "killStatus").mockRejectedValue(new ControlApiResponseError(503));

    render(<App />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_001);
    });

    expect(readPendingKill()).toEqual({ kind: "pending", commandId: losingId });
  });

  it("blocks this tab immediately when another tab reserves a kill identity", async () => {
    const commandId = "956b3f71-d41f-441b-bd12-7d83f879ef24";
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());

    render(<App />);
    await screen.findByText("100,000.00");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
        "aria-disabled",
        "false",
      ),
    );

    window.localStorage.setItem(pendingKillStorageKey, commandId);
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: pendingKillStorageKey,
        newValue: commandId,
        storageArea: window.localStorage,
      }),
    );

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
        "aria-disabled",
        "true",
      ),
    );
    expect(screen.getByText(new RegExp(commandId))).toBeInTheDocument();
  });

  it("fails closed when active-command discovery is unavailable", async () => {
    vi.mocked(controlApi.activeKill).mockRejectedValue(new TypeError("backend unavailable"));
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());

    render(<App />);

    expect(await screen.findByRole("button", { name: /retry active-command lookup/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it("fails closed when durable cross-tab command storage is unreadable", async () => {
    const storage = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(controlApi, "session").mockResolvedValue(SESSION);
    vi.spyOn(controlApi, "dashboard").mockResolvedValue(dashboardFixture());

    render(<App />);

    expect(
      await screen.findByText(/cross-tab command storage is unavailable or invalid/i),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /kill switch/i })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    storage.mockRestore();
  });
});
