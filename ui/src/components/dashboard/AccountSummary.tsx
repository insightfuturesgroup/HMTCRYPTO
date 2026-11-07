import {
  Activity,
  BadgeDollarSign,
  ChartNoAxesCombined,
  Gauge,
  Landmark,
  WalletCards,
  type LucideIcon,
} from "lucide-react";

import { type DashboardSnapshot } from "../../api/contracts";
import { displayDecimal, displayPercent, pnlTone } from "../../lib/format";

interface AccountSummaryProps {
  readonly snapshot: DashboardSnapshot | null;
}

interface SummaryItem {
  readonly icon: LucideIcon;
  readonly label: string;
  readonly value: string;
  readonly note: string;
  readonly tone?: "positive" | "negative" | "neutral";
}

/** Render the exchange-authoritative account metrics in the HelpMeTrade summary band. */
export function AccountSummary({ snapshot }: AccountSummaryProps) {
  const hasReconciledEvidence =
    snapshot?.reconciliationState === "READY_CLAIM" && snapshot.evidenceObservedAt !== null;
  const items: readonly SummaryItem[] = [
    {
      icon: Landmark,
      label: "Account equity",
      value: displayDecimal(snapshot?.equity ?? null),
      note: snapshot?.collateralAsset ?? "Collateral unavailable",
    },
    {
      icon: WalletCards,
      label: "Available balance",
      value: displayDecimal(snapshot?.availableBalance ?? null),
      note: hasReconciledEvidence ? "Exchange-reconciled" : "Reconciliation unavailable",
    },
    {
      icon: ChartNoAxesCombined,
      label: "Total exposure",
      value: displayDecimal(snapshot?.totalExposure ?? null),
      note: "Positions + open orders",
    },
    {
      icon: BadgeDollarSign,
      label: "Daily P&L",
      value: displayDecimal(snapshot?.dailyPnl ?? null),
      note: "UTC risk window",
      tone: pnlTone(snapshot?.dailyPnl ?? null),
    },
    {
      icon: Gauge,
      label: "Margin usage",
      value: displayPercent(snapshot?.marginUsagePercent ?? null),
      note: "Portfolio utilisation",
    },
    {
      icon: Activity,
      label: "Open positions",
      value: hasReconciledEvidence
        ? snapshot.positions.length.toLocaleString("en-IN")
        : "Unavailable",
      note: hasReconciledEvidence ? "Exchange-reconciled" : "Reconciliation unavailable",
    },
  ];

  return (
    <section aria-label="Account summary" className="hmt-summary-band">
      {items.map(({ icon: Icon, label, value, note, tone = "neutral" }) => (
        <article className="hmt-summary-cell" key={label}>
          <div className="hmt-summary-heading">
            <p className="hmt-summary-label">{label}</p>
            <Icon aria-hidden="true" className="hmt-summary-icon" size={18} />
          </div>
          <p className={`hmt-summary-value metric-${tone}`}>{value}</p>
          <p className="hmt-summary-note">{note}</p>
        </article>
      ))}
    </section>
  );
}

export type { AccountSummaryProps };
