import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ControlApiClient, ControlApiResponseError } from "../api/client";
import { readPendingKill } from "../lib/pendingKill";
import { KillSwitchControl } from "./KillSwitchControl";

function futurePreview(flatten: boolean) {
  return {
    previewId: "9cab76c9-814c-4873-9e0c-33b52de90c85",
    expiresAt: "2099-11-06T12:00:00Z",
    flatten,
    openOrderCount: 2,
    positionCount: 1,
    evidenceState: "RECONCILED_CLAIM" as const,
    evidenceObservedAt: "2026-11-06T12:00:00Z",
    warning: "Confirm the authoritative action",
  };
}

afterEach(() => vi.restoreAllMocks());

describe("KillSwitchControl", () => {
  it("requires preview and exact flatten phrase before one keyed submission", async () => {
    const api = new ControlApiClient();
    vi.spyOn(api, "previewKill").mockResolvedValue(futurePreview(true));
    const engage = vi
      .spyOn(api, "engageKill")
      .mockImplementation((_previewId, _flatten, _confirmation, _csrfToken, idempotencyKey) =>
        Promise.resolve({
          commandId: idempotencyKey,
          idempotencyKey,
          state: "QUEUED",
          acceptedAt: "2026-11-06T11:59:00Z",
        }),
      );
    const onObserved = vi.fn();
    const user = userEvent.setup();
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={onObserved} />);

    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));
    const engageButton = await screen.findByRole("button", { name: "ENGAGE KILL SWITCH" });
    expect(engageButton).toBeDisabled();
    await user.type(screen.getByLabelText(/type flatten_all_positions/i), "FLATTEN_ALL_POSITIONS");
    expect(engageButton).toBeEnabled();
    await user.click(engageButton);

    expect(engage).toHaveBeenCalledOnce();
    expect(engage).toHaveBeenCalledWith(
      "9cab76c9-814c-4873-9e0c-33b52de90c85",
      true,
      "FLATTEN_ALL_POSITIONS",
      "csrf-value",
      expect.stringMatching(/^[0-9a-f-]{36}$/),
      expect.any(AbortSignal),
    );
    expect(onObserved).toHaveBeenCalledWith(
      expect.objectContaining({ authoritative: true }),
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("blocks blind retry and reports one stable uncertain observation", async () => {
    const api = new ControlApiClient();
    vi.spyOn(api, "previewKill").mockResolvedValue(futurePreview(false));
    const engage = vi.spyOn(api, "engageKill").mockRejectedValue(new TypeError("network lost"));
    const onObserved = vi.fn();
    const user = userEvent.setup();
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={onObserved} />);

    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));
    await user.click(await screen.findByRole("button", { name: "ENGAGE KILL SWITCH" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/blind retry is blocked/i);
    expect(screen.queryByRole("button", { name: "ENGAGE KILL SWITCH" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /close and monitor status/i })).toBeEnabled();
    expect(engage).toHaveBeenCalledOnce();
    const submittedKey = engage.mock.calls[0]?.[4];
    expect(onObserved).toHaveBeenCalledOnce();
    expect(onObserved).toHaveBeenCalledWith(
      expect.objectContaining({
        idempotencyKey: submittedKey,
        state: "UNCERTAIN",
        authoritative: false,
      }),
    );
    expect(readPendingKill()).toEqual({ kind: "pending", commandId: submittedKey });
  });

  it("rechecks expiry against a fresh clock at the exact engage attempt", async () => {
    const api = new ControlApiClient();
    const expiresAt = Date.parse("2030-01-01T00:00:00Z");
    const clock = vi.spyOn(Date, "now").mockReturnValue(expiresAt - 500);
    vi.spyOn(api, "previewKill").mockResolvedValue({
      ...futurePreview(false),
      expiresAt: "2030-01-01T00:00:00Z",
    });
    const engage = vi.spyOn(api, "engageKill");
    const user = userEvent.setup();
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));
    const engageButton = await screen.findByRole("button", { name: "ENGAGE KILL SWITCH" });
    clock.mockReturnValue(expiresAt + 1);
    await user.click(engageButton);

    expect(screen.getByRole("alert")).toHaveTextContent(/safety preview expired/i);
    expect(engage).not.toHaveBeenCalled();
  });

  it("clears the reserved identity after a definitive pre-admission rejection", async () => {
    const api = new ControlApiClient();
    vi.spyOn(api, "previewKill").mockResolvedValue(futurePreview(false));
    vi.spyOn(api, "engageKill").mockRejectedValue(new ControlApiResponseError(409));
    const onObserved = vi.fn();
    const user = userEvent.setup();
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={onObserved} />);

    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));
    await user.click(await screen.findByRole("button", { name: "ENGAGE KILL SWITCH" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/rejected.*before admission/i);
    expect(readPendingKill()).toEqual({ kind: "clear" });
    expect(onObserved).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /create safety preview/i })).toBeEnabled();
  });

  it("expires previews locally before an engage request", async () => {
    const api = new ControlApiClient();
    vi.spyOn(api, "previewKill").mockResolvedValue({
      ...futurePreview(false),
      expiresAt: "2020-01-01T00:00:00Z",
    });
    const engage = vi.spyOn(api, "engageKill");
    const user = userEvent.setup();
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/preview has expired/i);
    expect(screen.queryByRole("button", { name: "ENGAGE KILL SWITCH" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /discard expired preview/i })).toBeEnabled();
    expect(engage).not.toHaveBeenCalled();
  });

  it("traps the modal semantically, closes on Escape and restores focus", async () => {
    const api = new ControlApiClient();
    const user = userEvent.setup();
    const root = document.createElement("div");
    root.id = "root";
    document.body.append(root);
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={vi.fn()} />, {
      container: root,
    });
    const trigger = screen.getByRole("button", { name: /kill switch/i });

    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: /global kill switch/i });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(root).toHaveAttribute("inert");
    await waitFor(() => expect(screen.getByRole("button", { name: /close kill switch/i })).toHaveFocus());
    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(root).not.toHaveAttribute("inert");
    expect(trigger).toHaveFocus();
  });

  it("allows a pending preview request to be cancelled by closing", async () => {
    const api = new ControlApiClient();
    let observedSignal: AbortSignal | undefined;
    vi.spyOn(api, "previewKill").mockImplementation(
      (_flatten, _csrfToken, signal) =>
        new Promise((_resolve, reject) => {
          observedSignal = signal;
          signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const user = userEvent.setup();
    render(<KillSwitchControl api={api} csrfToken="csrf-value" onObserved={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /kill switch/i }));
    await user.click(screen.getByRole("button", { name: /create safety preview/i }));
    await user.click(screen.getByRole("button", { name: /close kill switch/i }));

    expect(observedSignal?.aborted).toBe(true);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
