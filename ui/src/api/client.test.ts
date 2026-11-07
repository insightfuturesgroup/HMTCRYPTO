import { afterEach, describe, expect, it, vi } from "vitest";

import { dashboardFixture } from "../test/fixtures";
import pythonDashboardView from "../test/python_dashboard_view.json";
import { ControlApiClient, ControlApiResponseError } from "./client";
import { ApiContractError } from "./contracts";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function response(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function previewResponse(flatten = false): Record<string, unknown> {
  return {
    previewId: "9cab76c9-814c-4873-9e0c-33b52de90c85",
    expiresAt: "2026-11-06T12:00:00Z",
    flatten,
    openOrderCount: 2,
    positionCount: 1,
    evidenceState: "RECONCILED_CLAIM",
    evidenceObservedAt: "2026-11-06T12:00:00Z",
    warning: "Orders will be cancelled",
  };
}

describe("ControlApiClient transport", () => {
  it("pins requests to same-origin, no-store, no-redirect transport", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response(previewResponse()));
    vi.stubGlobal("fetch", fetchMock);

    await new ControlApiClient().previewKill(false, "csrf-value");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/commands/kill/preview",
      expect.objectContaining({
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        referrerPolicy: "no-referrer",
        method: "POST",
        headers: expect.objectContaining({ "X-CSRF-Token": "csrf-value" }),
      }),
    );
  });

  it("rejects non-relative API base paths", () => {
    expect(() => new ControlApiClient("https://evil.invalid/api")).toThrow(/same-origin/);
    expect(() => new ControlApiClient("//evil.invalid/api")).toThrow(/same-origin/);
    expect(() => new ControlApiClient("/api/../private")).toThrow(/same-origin/);
    expect(() => new ControlApiClient("/\\evil.invalid/api")).toThrow(/same-origin/);
  });

  it("aborts a request at the bounded client timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
      ),
    );
    const pending = new ControlApiClient("/api/v1", 100).session();
    const rejection = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    await vi.advanceTimersByTimeAsync(101);
    await rejection;
  });
});

describe("ControlApiClient contracts", () => {
  it("parses the exact readiness snapshot returned with 503 without accepting 503 elsewhere", async () => {
    const unavailableHealth = {
      live: true,
      ready: false,
      sequence: 7,
      observedAt: "2026-11-06T12:00:00Z",
      components: [
        {
          component: "reconciliation",
          state: "failed",
          reason: "evidence_stale",
          observedAt: "2026-11-06T12:00:00Z",
        },
      ],
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(unavailableHealth, 503)));
    await expect(new ControlApiClient().health()).resolves.toEqual(unavailableHealth);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(dashboardFixture(), 503)));
    await expect(new ControlApiClient().dashboard()).rejects.toEqual(
      new ControlApiResponseError(503),
    );
  });

  it("rejects a malformed readiness body even when the API correctly uses status 503", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response(
          {
            live: "true",
            ready: false,
            sequence: 0,
            observedAt: "2026-11-06T12:00:00Z",
            components: [],
          },
          503,
        ),
      ),
    );
    await expect(new ControlApiClient().health()).rejects.toBeInstanceOf(ApiContractError);
  });

  it("rejects credential-shaped response fields before returning data", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          authenticated: true,
          csrfToken: "csrf-value",
          absoluteExpiresAt: "2026-11-06T12:00:00Z",
          api_secret: "must-not-enter-browser",
        }),
      ),
    );
    await expect(new ControlApiClient().session()).rejects.toBeInstanceOf(ApiContractError);
  });

  it.each(["secret", "deltaKey", "access_token", "password", "sessionCookie"])(
    "rejects alternate sensitive response key %s",
    async (sensitiveKey) => {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(
          response({
            authenticated: true,
            csrfToken: "csrf-value",
            absoluteExpiresAt: "2026-11-06T12:00:00Z",
            [sensitiveKey]: "must-not-enter-browser",
          }),
        ),
      );
      await expect(new ControlApiClient().session()).rejects.toBeInstanceOf(ApiContractError);
    },
  );

  it("deeply rejects truthy strings that could mislabel protection or reduce-only state", async () => {
    const unsafe = structuredClone(dashboardFixture()) as unknown as Record<string, unknown>;
    const positions = unsafe.positions as Record<string, unknown>[];
    positions[0].protectionState = "false";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(unsafe)));
    await expect(new ControlApiClient().dashboard()).rejects.toBeInstanceOf(ApiContractError);

    const unsafeOrder = structuredClone(dashboardFixture()) as unknown as Record<string, unknown>;
    const orders = unsafeOrder.openOrders as Record<string, unknown>[];
    orders[0].reduceOnly = "false";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(unsafeOrder)));
    await expect(new ControlApiClient().dashboard()).rejects.toBeInstanceOf(ApiContractError);
  });

  it("rejects unknown region, environment, mode and extra fields", async () => {
    for (const [key, value] of [
      ["region", "moon"],
      ["environment", "production"],
      ["mode", "REAL"],
    ] as const) {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(response({ ...dashboardFixture(), [key]: value })),
      );
      await expect(new ControlApiClient().dashboard()).rejects.toBeInstanceOf(ApiContractError);
    }
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(response({ ...dashboardFixture(), unexpected: true })),
    );
    await expect(new ControlApiClient().dashboard()).rejects.toBeInstanceOf(ApiContractError);
  });

  it("accepts the exact dashboard_view wire fixture emitted by Python", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(pythonDashboardView)));
    await expect(new ControlApiClient().dashboard()).resolves.toEqual(pythonDashboardView);
  });

  it("uses only the closed persisted reconciliation vocabulary", async () => {
    for (const state of ["UNKNOWN", "BLOCKED", "READY_CLAIM"] as const) {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(response(dashboardFixture({ reconciliationState: state }))),
      );
      await expect(new ControlApiClient().dashboard()).resolves.toMatchObject({
        reconciliationState: state,
      });
    }
    for (const state of ["CURRENT", "RUNNING", "STALE", "FAILED", "DISABLED"]) {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockResolvedValue(response({ ...dashboardFixture(), reconciliationState: state })),
      );
      await expect(new ControlApiClient().dashboard()).rejects.toBeInstanceOf(ApiContractError);
    }
  });

  it("rejects a kill preview whose authoritative mode differs from the request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(previewResponse(false))));
    await expect(new ControlApiClient().previewKill(true, "csrf-value")).rejects.toBeInstanceOf(
      ApiContractError,
    );
  });

  it("rejects calendar-impossible UTC instants instead of accepting Date normalization", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          authenticated: true,
          csrfToken: "csrf-value",
          absoluteExpiresAt: "2026-02-31T00:00:00Z",
        }),
      ),
    );
    await expect(new ControlApiClient().session()).rejects.toBeInstanceOf(ApiContractError);
  });

  it("binds a supplied idempotency key and validates command status", async () => {
    const idempotencyKey = "10000000-0000-4000-8000-000000000001";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          commandId: idempotencyKey,
          state: "QUEUED",
          acceptedAt: "2026-11-06T12:00:00Z",
        }),
      )
      .mockResolvedValueOnce(
        response({
          commandId: idempotencyKey,
          state: "COMPLETED",
          acceptedAt: "2026-11-06T12:00:00Z",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlApiClient();

    const accepted = await client.engageKill(
      "9cab76c9-814c-4873-9e0c-33b52de90c85",
      false,
      null,
      "csrf-value",
      idempotencyKey,
    );
    const completed = await client.killStatus(idempotencyKey);

    expect(accepted.idempotencyKey).toBe(idempotencyKey);
    expect(completed).toMatchObject({ state: "COMPLETED", authoritative: true });
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        headers: expect.objectContaining({ "Idempotency-Key": idempotencyKey }),
      }),
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(`/api/v1/commands/${idempotencyKey}`);
    expect(fetchMock.mock.calls[1]?.[1]).toEqual(expect.objectContaining({ method: "GET" }));
  });

  it("rejects a command response not bound to the submitted idempotency identity", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          commandId: "20000000-0000-4000-8000-000000000002",
          state: "QUEUED",
          acceptedAt: "2026-11-06T12:00:00Z",
        }),
      ),
    );
    await expect(
      new ControlApiClient().engageKill(
        "9cab76c9-814c-4873-9e0c-33b52de90c85",
        false,
        null,
        "csrf-value",
        "10000000-0000-4000-8000-000000000001",
      ),
    ).rejects.toBeInstanceOf(ApiContractError);
  });

  it("loads the exact authenticated active-kill envelope and validates its identity", async () => {
    const commandId = "39b7f399-4e00-47da-8e80-7fd4c813f94a";
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          active: {
            commandId,
            state: "CLAIMED",
            acceptedAt: "2026-11-06T12:00:00Z",
          },
        }),
      )
      .mockResolvedValueOnce(response({ active: null }));
    vi.stubGlobal("fetch", fetchMock);
    const client = new ControlApiClient();

    await expect(client.activeKill()).resolves.toEqual({
      commandId,
      idempotencyKey: commandId,
      state: "CLAIMED",
      acceptedAt: "2026-11-06T12:00:00Z",
      authoritative: true,
    });
    await expect(client.activeKill()).resolves.toBeNull();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/commands/kill/active");
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ method: "GET" }));
  });

  it("rejects non-Python UUIDs in command and dashboard identities", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        response({
          active: {
            commandId: "not-a-uuid",
            state: "QUEUED",
            acceptedAt: "2026-11-06T12:00:00Z",
          },
        }),
      ),
    );
    await expect(new ControlApiClient().activeKill()).rejects.toBeInstanceOf(ApiContractError);

    const unsafeDashboard = structuredClone(dashboardFixture()) as unknown as Record<string, unknown>;
    (unsafeDashboard.openOrders as Record<string, unknown>[])[0].localOrderId = "local-order-1";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(unsafeDashboard)));
    await expect(new ControlApiClient().dashboard()).rejects.toBeInstanceOf(ApiContractError);
  });

  it("rejects oversized, non-JSON and non-JSON-content-type responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("not-json", { status: 200 })));
    await expect(new ControlApiClient().session()).rejects.toBeInstanceOf(ApiContractError);

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ authenticated: false }), {
          status: 200,
          headers: { "Content-Type": "text/plain" },
        }),
      ),
    );
    await expect(new ControlApiClient().session()).rejects.toBeInstanceOf(ApiContractError);
  });
});
