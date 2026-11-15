import { describe, expect, it } from "vitest";

import { queryStaleTime } from "./queryClient";

describe("queryStaleTime", () => {
  it("never caches live account state URLs with view query strings", () => {
    expect(queryStaleTime("/api/v1/accounts/account-1/state?view=orders")).toBe(0);
    expect(queryStaleTime("/api/v1/accounts/account-1/state?view=positions")).toBe(0);
  });

  it("never caches mutable order queues", () => {
    expect(queryStaleTime("/api/v1/accounts/account-1/order-intents")).toBe(0);
    expect(queryStaleTime("/api/v1/trading/actions?account_id=account-1")).toBe(0);
  });
});
