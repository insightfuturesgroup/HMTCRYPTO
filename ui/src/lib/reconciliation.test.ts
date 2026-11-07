import { describe, expect, it } from "vitest";

import { RECONCILIATION_PRESENTATION } from "./reconciliation";

describe("reconciliation presentation", () => {
  it("maps every persisted/API state explicitly and grants readiness only to READY_CLAIM", () => {
    expect(RECONCILIATION_PRESENTATION).toEqual({
      UNKNOWN: { label: "Unknown", ready: false },
      BLOCKED: { label: "Blocked", ready: false },
      READY_CLAIM: { label: "Current", ready: true },
    });
  });
});
