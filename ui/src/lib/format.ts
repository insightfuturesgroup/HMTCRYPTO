const DECIMAL = /^(-?)(0|[1-9]\d*)(?:\.(\d+))?$/;
const MAX_DISPLAY_DIGITS = 12;

function incrementInteger(value: string): string {
  const digits = value.split("");
  for (let index = digits.length - 1; index >= 0; index -= 1) {
    const digit = digits[index];
    if (digit === undefined) break;
    if (digit !== "9") {
      digits[index] = String.fromCharCode(digit.charCodeAt(0) + 1);
      return digits.join("");
    }
    digits[index] = "0";
  }
  return `1${digits.join("")}`;
}

function groupInteger(value: string): string {
  let grouped = "";
  for (let index = 0; index < value.length; index += 1) {
    if (index > 0 && (value.length - index) % 3 === 0) grouped += ",";
    grouped += value[index];
  }
  return grouped;
}

/** Render a canonical decimal string without converting it to binary floating point. */
export function displayDecimal(value: string | null, digits = 2): string {
  if (
    value === null ||
    !Number.isSafeInteger(digits) ||
    digits < 0 ||
    digits > MAX_DISPLAY_DIGITS
  ) {
    return "Unavailable";
  }
  const match = DECIMAL.exec(value);
  if (match === null) return "Unavailable";

  const negative = match[1] === "-";
  let integer = match[2] ?? "0";
  const sourceFraction = match[3] ?? "";
  let fraction = sourceFraction.slice(0, digits).padEnd(digits, "0");
  const roundUp = sourceFraction.length > digits && (sourceFraction[digits] ?? "0") >= "5";

  if (roundUp) {
    if (digits === 0) {
      integer = incrementInteger(integer);
    } else {
      const combined = incrementInteger(`${integer}${fraction}`);
      const padded = combined.padStart(integer.length + digits, "0");
      integer = padded.slice(0, -digits);
      fraction = padded.slice(-digits);
    }
  }

  const nonZero = /[1-9]/.test(integer) || /[1-9]/.test(sourceFraction);
  const sign = negative && nonZero ? "-" : "";
  const suffix = digits === 0 ? "" : `.${fraction}`;
  return `${sign}${groupInteger(integer)}${suffix}`;
}

export function displayPercent(value: string | null, digits = 2): string {
  const rendered = displayDecimal(value, digits);
  return rendered === "Unavailable" ? rendered : `${rendered}%`;
}

export function pnlTone(value: string | null): "positive" | "negative" | "neutral" {
  if (value === null) return "neutral";
  const match = DECIMAL.exec(value);
  if (match === null || !/[1-9]/.test(`${match[2] ?? ""}${match[3] ?? ""}`)) return "neutral";
  return match[1] === "-" ? "negative" : "positive";
}
