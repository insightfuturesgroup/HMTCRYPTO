import { useEffect, useState } from "react";

const FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

export function useUtcClock(): string {
  const [now, setNow] = useState(() => FORMATTER.format(new Date()));
  useEffect(() => {
    const timer = window.setInterval(() => setNow(FORMATTER.format(new Date())), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  return `${now} UTC`;
}
