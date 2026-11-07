import { describe, expect, it } from "vitest";

import { displayDecimal, displayPercent, pnlTone } from "./format";

describe("display formatting", () => {
  it("formats bounded decimal strings without inventing unavailable values", () => {
    expect(displayDecimal("1234.5")).toBe("1,234.50");
    expect(displayPercent("1.2345", 2)).toBe("1.23%");
    expect(displayDecimal(null)).toBe("Unavailable");
    expect(displayDecimal("NaN")).toBe("Unavailable");
    expect(displayDecimal("1e9")).toBe("Unavailable");
  });

  it("formats and rounds arbitrary-precision values without a Number conversion", () => {
    expect(displayDecimal("90071992547409931234567890.125", 2)).toBe(
      "90,071,992,547,409,931,234,567,890.13",
    );
    expect(displayDecimal("999999999999999999999999.995", 2)).toBe(
      "1,000,000,000,000,000,000,000,000.00",
    );
    expect(displayDecimal("0.00000000000000000001", 8)).toBe("0.00000000");
    expect(displayDecimal("-0.00000000000000000001", 8)).toBe("-0.00000000");
    expect(displayDecimal("-12345678901234567890.999", 2)).toBe(
      "-12,345,678,901,234,567,891.00",
    );
  });

  it("classifies PnL conservatively", () => {
    expect(pnlTone("0.01")).toBe("positive");
    expect(pnlTone("-0.01")).toBe("negative");
    expect(pnlTone("0.00000000000000000001")).toBe("positive");
    expect(pnlTone("-0.00000000000000000001")).toBe("negative");
    expect(pnlTone("90071992547409931234567890")).toBe("positive");
    expect(pnlTone("-90071992547409931234567890")).toBe("negative");
    expect(pnlTone("0")).toBe("neutral");
    expect(pnlTone("-0.0000")).toBe("neutral");
    expect(pnlTone(null)).toBe("neutral");
  });
});
