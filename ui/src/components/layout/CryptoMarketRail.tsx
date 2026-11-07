import { Activity, Database, Radio, ShieldCheck } from "lucide-react";

import { type DashboardSnapshot, type PositionView } from "../../api/contracts";
import { displayDecimal, displayPercent } from "../../lib/format";

export interface CryptoMarketRailProps {
  readonly snapshot: DashboardSnapshot | null;
  readonly reconciliation: {
    readonly label: string;
    readonly ready: boolean;
  } | null;
}

interface MarketRow {
  readonly symbol: string;
  readonly position: PositionView | null;
  readonly openOrderCount: number;
}

function marketRows(snapshot: DashboardSnapshot | null): readonly MarketRow[] {
  if (snapshot === null) return [];

  const rows = new Map<string, MarketRow>();
  for (const position of snapshot.positions) {
    const key = position.symbol.trim().toUpperCase();
    if (key.length === 0 || rows.has(key)) continue;
    rows.set(key, {
      symbol: position.symbol,
      position,
      openOrderCount: 0,
    });
  }
  for (const order of snapshot.openOrders) {
    const key = order.symbol.trim().toUpperCase();
    if (key.length === 0) continue;
    const current = rows.get(key);
    rows.set(key, {
      symbol: current?.symbol ?? order.symbol,
      position: current?.position ?? null,
      openOrderCount: (current?.openOrderCount ?? 0) + 1,
    });
  }

  return [...rows.values()].sort((left, right) => left.symbol.localeCompare(right.symbol));
}

function EvidenceRow({
  active,
  icon: Icon,
  label,
  value,
}: {
  readonly active: boolean;
  readonly icon: typeof Radio;
  readonly label: string;
  readonly value: string;
}) {
  return (
    <div className="market-rail__evidence-row">
      <Icon aria-hidden="true" size={15} />
      <span>{label}</span>
      <strong className={active ? "is-ready" : "is-blocked"}>{value}</strong>
    </div>
  );
}

/** Read-only market evidence derived exclusively from the dashboard snapshot. */
export function CryptoMarketRail({ snapshot, reconciliation }: CryptoMarketRailProps) {
  const rows = marketRows(snapshot);
  const hasReconciledEvidence =
    snapshot?.reconciliationState === "READY_CLAIM" && snapshot.evidenceObservedAt !== null;

  return (
    <aside aria-label="Crypto market overview" className="market-rail">
      <div className="market-rail__heading">
        <div>
          <p className="market-rail__eyebrow">Market</p>
          <h2>Crypto watchlist</h2>
        </div>
        <span className="market-rail__venue">Delta</span>
      </div>

      <section aria-label="Exchange evidence" className="market-rail__evidence">
        <EvidenceRow
          active={snapshot?.streamConnected === true}
          icon={Radio}
          label="Market stream"
          value={snapshot?.streamConnected === true ? "Connected" : "Unavailable"}
        />
        <EvidenceRow
          active={snapshot?.catalogueFresh === true}
          icon={Database}
          label="Catalogue"
          value={snapshot?.catalogueFresh === true ? "Fresh" : "Unavailable"}
        />
        <EvidenceRow
          active={reconciliation?.ready === true}
          icon={Activity}
          label="Reconciliation"
          value={reconciliation?.label ?? "Unavailable"}
        />
        <EvidenceRow
          active={snapshot?.riskHalted === false}
          icon={ShieldCheck}
          label="Risk entries"
          value={snapshot === null ? "Unavailable" : snapshot.riskHalted ? "Halted" : "Open"}
        />
      </section>

      <div className="market-rail__list" role="list">
        {rows.length > 0 ? (
          rows.map((row) => (
            <article className="market-rail__product" key={row.symbol} role="listitem">
              <div className="market-rail__product-heading">
                <div>
                  <strong>{row.symbol}</strong>
                  <small>
                    {row.position === null
                      ? `${row.openOrderCount} open order${row.openOrderCount === 1 ? "" : "s"}`
                      : `${row.position.side} · ${row.position.contracts} contracts`}
                  </small>
                </div>
                {row.position === null ? null : (
                  <span className={`market-rail__side market-rail__side--${row.position.side}`}>
                    {row.position.side}
                  </span>
                )}
              </div>
              <dl className="market-rail__quotes">
                <div>
                  <dt>Last</dt>
                  <dd>{displayDecimal(row.position?.lastPrice ?? null)}</dd>
                </div>
                <div>
                  <dt>Mark</dt>
                  <dd>{displayDecimal(row.position?.markPrice ?? null)}</dd>
                </div>
                <div>
                  <dt>Funding</dt>
                  <dd>{displayPercent(row.position?.fundingRate ?? null, 4)}</dd>
                </div>
              </dl>
              {row.openOrderCount > 0 && row.position !== null ? (
                <p className="market-rail__orders">
                  {row.openOrderCount} open order{row.openOrderCount === 1 ? "" : "s"}
                </p>
              ) : null}
            </article>
          ))
        ) : (
          <p className="market-rail__empty">
            {hasReconciledEvidence ? "No active products" : "Market evidence unavailable"}
          </p>
        )}
      </div>
    </aside>
  );
}
