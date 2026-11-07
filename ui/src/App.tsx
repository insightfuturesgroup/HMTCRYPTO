import {
  Activity,
  BookOpen,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { controlApi, ControlApiResponseError } from "./api/client";
import {
  type DashboardSnapshot,
  type KillCommandObservation,
  type OperatorSessionView,
  type ProtectionState,
} from "./api/contracts";
import { AccountSummary } from "./components/dashboard/AccountSummary";
import { CryptoMarketRail } from "./components/layout/CryptoMarketRail";
import {
  type DashboardView,
  HelpmeTradeHeader,
} from "./components/layout/HelpmeTradeHeader";
import { KillSwitchControl } from "./components/KillSwitchControl";
import { useUtcClock } from "./hooks/useUtcClock";
import { displayDecimal, displayPercent, pnlTone } from "./lib/format";
import {
  adoptAuthoritativePendingKill,
  clearPendingKill,
  type PendingKillState,
  persistPendingKill,
  readPendingKill,
  subscribePendingKill,
} from "./lib/pendingKill";
import { RECONCILIATION_PRESENTATION } from "./lib/reconciliation";

const DASHBOARD_REFRESH_MS = 5_000;
const COMMAND_POLL_MS = 2_000;
const MAX_COMMAND_STATUS_FAILURES = 5;
const TERMINAL_COMMAND_STATES = new Set(["COMPLETED", "FAILED"] as const);
const PROTECTION_PRESENTATION: Record<
  ProtectionState,
  { readonly label: string; readonly className: string }
> = {
  UNKNOWN: { label: "Unknown", className: "text-slate-600" },
  UNPROTECTED: { label: "Unprotected", className: "font-semibold text-red-700" },
  VERIFYING: { label: "Verifying", className: "font-semibold text-amber-700" },
  PAPER_SIMULATED: { label: "Paper simulated", className: "text-teal-700" },
  NATIVE_VERIFIED: { label: "Native verified", className: "text-emerald-700" },
  SYNTHETIC_MONITORING: {
    label: "Synthetic monitoring",
    className: "font-semibold text-amber-700",
  },
  STALE: { label: "Stale", className: "font-semibold text-red-700" },
};

function hasReconciledDashboardEvidence(snapshot: DashboardSnapshot | null): boolean {
  return (
    snapshot?.reconciliationState === "READY_CLAIM" && snapshot.evidenceObservedAt !== null
  );
}

function Login({
  onAuthenticated,
}: {
  readonly onAuthenticated: (value: OperatorSessionView) => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onAuthenticated(await controlApi.login(password));
      setPassword("");
    } catch {
      setError("Authentication failed. Check the operator credential and server status.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="hmt-auth">
      <section className="hmt-auth-card">
        <div className="hmt-auth-brand">
          <span aria-hidden="true" className="hmt-auth-mark">
            <TrendingUp size={25} strokeWidth={2.25} />
          </span>
          <div>
            <h1>Helpme Trade</h1>
            <p>Crypto operator console</p>
          </div>
        </div>
        <div className="hmt-auth-notice">
          Exchange credentials are server-only and never entered here.
        </div>
        <form className="hmt-auth-form" onSubmit={(event) => void submit(event)}>
          <label htmlFor="password">
            Operator password
          </label>
          <input
            autoComplete="current-password"
            id="password"
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
          <button
            className="hmt-auth-submit"
            disabled={busy || password.length < 12}
            type="submit"
          >
            {busy ? "Authenticating…" : "Sign in"}
          </button>
          {error ? (
            <p className="hmt-auth-error" role="alert">
              {error}
            </p>
          ) : null}
        </form>
      </section>
    </main>
  );
}

function StatusPill({
  active,
  children,
  state,
}: {
  readonly active: boolean;
  readonly children: string;
  readonly state: string;
}) {
  return (
    <span
      aria-label={`${children}: ${state}`}
      className={`status-pill ${active ? "status-pill--good" : "status-pill--bad"}`}
    >
      <span aria-hidden="true" className="status-dot" />
      <span>{children}</span>
      <strong>{state}</strong>
    </span>
  );
}

function Positions({ snapshot }: { readonly snapshot: DashboardSnapshot | null }) {
  return (
    <section aria-labelledby="positions-title" className="data-panel">
      <div className="data-panel__header">
        <div>
          <h2 id="positions-title">Open positions</h2>
          <p>Last/mark, leverage, liquidation, funding and verified protection</p>
        </div>
        <Activity aria-hidden="true" className="text-teal-700" size={20} />
      </div>
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th scope="col">Product</th>
              <th scope="col">Side / contracts</th>
              <th scope="col">Entry</th>
              <th scope="col">Last / mark</th>
              <th scope="col">Leverage</th>
              <th scope="col">Liquidation</th>
              <th scope="col">Funding</th>
              <th scope="col">Margin</th>
              <th scope="col">Unrealised PnL</th>
              <th scope="col">Realised PnL</th>
              <th scope="col">Protection</th>
            </tr>
          </thead>
          <tbody>
            {snapshot?.positions.length ? (
              snapshot.positions.map((position) => (
                <tr key={position.productId}>
                  <td>
                    <strong>{position.symbol}</strong>
                    <small>#{position.productId}</small>
                  </td>
                  <td>
                    <span className={`side side--${position.side}`}>{position.side}</span>
                    <small>{position.contracts} contracts</small>
                  </td>
                  <td>{displayDecimal(position.entryPrice)}</td>
                  <td>
                    {displayDecimal(position.lastPrice)}
                    <small>Mark {displayDecimal(position.markPrice)}</small>
                  </td>
                  <td>{displayDecimal(position.leverage)}×</td>
                  <td>{displayDecimal(position.liquidationPrice)}</td>
                  <td>{displayPercent(position.fundingRate, 4)}</td>
                  <td>{displayDecimal(position.marginUsed)}</td>
                  <td className={`metric-${pnlTone(position.unrealisedPnl)}`}>
                    {displayDecimal(position.unrealisedPnl)}
                  </td>
                  <td className={`metric-${pnlTone(position.realisedPnl)}`}>
                    {displayDecimal(position.realisedPnl)}
                  </td>
                  <td>
                    <span className={PROTECTION_PRESENTATION[position.protectionState].className}>
                      {PROTECTION_PRESENTATION[position.protectionState].label}
                    </span>
                    <small>{position.protectionObservedAt ?? "No protection evidence"}</small>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td className="empty-cell" colSpan={11}>
                  {hasReconciledDashboardEvidence(snapshot)
                    ? "No open positions"
                    : "Position evidence unavailable"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Orders({ snapshot }: { readonly snapshot: DashboardSnapshot | null }) {
  return (
    <section aria-labelledby="orders-title" className="data-panel">
      <div className="data-panel__header">
        <div>
          <h2 id="orders-title">Open orders</h2>
          <p>Durable local and exchange identities</p>
        </div>
        <BookOpen aria-hidden="true" className="text-teal-700" size={20} />
      </div>
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th scope="col">Symbol</th>
              <th scope="col">Side</th>
              <th scope="col">Type</th>
              <th scope="col">Contracts</th>
              <th scope="col">Price</th>
              <th scope="col">Status</th>
              <th scope="col">Flags</th>
            </tr>
          </thead>
          <tbody>
            {snapshot?.openOrders.length ? (
              snapshot.openOrders.map((order) => (
                <tr key={order.localOrderId}>
                  <td>
                    <strong>{order.symbol}</strong>
                    <small>{order.clientOrderId}</small>
                  </td>
                  <td>{order.side}</td>
                  <td>{order.orderType}</td>
                  <td>{order.contracts}</td>
                  <td>{displayDecimal(order.limitPrice)}</td>
                  <td>{order.status}</td>
                  <td>{order.reduceOnly ? "Reduce only" : "Risk increasing"}</td>
                </tr>
              ))
            ) : (
              <tr>
                <td className="empty-cell" colSpan={7}>
                  {hasReconciledDashboardEvidence(snapshot)
                    ? "No open orders"
                    : "Order evidence unavailable"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function RiskPosture({ snapshot }: { readonly snapshot: DashboardSnapshot | null }) {
  const reconciliation =
    snapshot === null ? null : RECONCILIATION_PRESENTATION[snapshot.reconciliationState];
  return (
    <section aria-labelledby="risk-title" className="data-panel">
      <div className="data-panel__header">
        <div>
          <h2 id="risk-title">Safety posture</h2>
          <p>Current server-side gates</p>
        </div>
        <ShieldCheck aria-hidden="true" className="text-teal-700" size={20} />
      </div>
      <dl className="space-y-4 p-5 text-sm">
        <div>
          <dt>Account</dt>
          <dd>{snapshot?.accountLabel ?? "Unavailable"}</dd>
        </div>
        <div>
          <dt>Risk latch</dt>
          <dd>{snapshot?.riskHalted ? "HALTED" : snapshot ? "Open under policy" : "Unavailable"}</dd>
        </div>
        <div>
          <dt>Reconciliation</dt>
          <dd>
            {reconciliation === null
              ? "Unavailable"
              : `${reconciliation.label} (${snapshot?.reconciliationState})`}
          </dd>
        </div>
        <div>
          <dt>Last evidence</dt>
          <dd>{snapshot?.evidenceObservedAt ?? "Unavailable"}</dd>
        </div>
        <div>
          <dt>Snapshot generated</dt>
          <dd>{snapshot?.generatedAt ?? "Unavailable"}</dd>
        </div>
      </dl>
    </section>
  );
}

function isAuthoritativeTerminal(command: KillCommandObservation | null): boolean {
  return (
    command !== null &&
    command.authoritative &&
    TERMINAL_COMMAND_STATES.has(command.state as "COMPLETED" | "FAILED")
  );
}

function restoredPendingCommand(state: PendingKillState): KillCommandObservation | null {
  if (state.kind !== "pending") return null;
  return {
    commandId: state.commandId,
    idempotencyKey: state.commandId,
    state: "UNCERTAIN",
    acceptedAt: null,
    authoritative: false,
  };
}

function Dashboard({
  session,
  onLogout,
}: {
  readonly session: OperatorSessionView;
  readonly onLogout: () => void;
}) {
  const utc = useUtcClock();
  const [activeView, setActiveView] = useState<DashboardView>("overview");
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingKill, setPendingKill] = useState<PendingKillState>(readPendingKill);
  const [command, setCommand] = useState<KillCommandObservation | null>(() =>
    restoredPendingCommand(pendingKill),
  );
  const [commandStatusError, setCommandStatusError] = useState<string | null>(null);
  const [commandStatusFailures, setCommandStatusFailures] = useState(0);
  const [activeLookupState, setActiveLookupState] = useState<"checking" | "ready" | "failed">(
    "checking",
  );
  const [activeLookupError, setActiveLookupError] = useState<string | null>(null);
  const [storageError, setStorageError] = useState<string | null>(
    pendingKill.kind === "unavailable"
      ? "Cross-tab command storage is unavailable or invalid. New kill commands are blocked."
      : null,
  );
  const [serverActivePresent, setServerActivePresent] = useState(false);
  const [loading, setLoading] = useState(true);
  const lastSequence = useRef(-1);
  const activeLookupRevision = useRef(0);

  const refresh = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    try {
      const next = await controlApi.dashboard();
      if (next.sequence < lastSequence.current) throw new Error("Dashboard sequence moved backwards");
      lastSequence.current = next.sequence;
      setSnapshot(next);
      setError(null);
    } catch {
      setSnapshot(null);
      setError("Dashboard evidence is unavailable. Risk-increasing controls remain disabled.");
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(false), DASHBOARD_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const refreshActiveKill = useCallback(async (signal?: AbortSignal) => {
    const revision = activeLookupRevision.current + 1;
    activeLookupRevision.current = revision;
    setActiveLookupState("checking");
    setActiveLookupError(null);
    try {
      const active = await controlApi.activeKill(signal);
      if (signal?.aborted || revision !== activeLookupRevision.current) return;
      const local = readPendingKill();
      setPendingKill(local);
      if (local.kind === "unavailable") {
        setStorageError(
          "Cross-tab command storage is unavailable or invalid. New kill commands are blocked.",
        );
      } else {
        setStorageError(null);
      }
      if (active !== null) {
        setServerActivePresent(true);
        setCommand(active);
        if (!adoptAuthoritativePendingKill(active.commandId)) {
          setPendingKill({ kind: "unavailable" });
          setStorageError(
            "The active server command could not be reserved across tabs. New kill commands are blocked.",
          );
        } else {
          setPendingKill({ kind: "pending", commandId: active.commandId });
          setStorageError(null);
        }
      } else {
        setServerActivePresent(false);
        if (local.kind === "pending") {
          setCommand((current) =>
            current?.commandId === local.commandId ? current : restoredPendingCommand(local),
          );
        } else if (local.kind === "clear") {
          setCommand((current) => (isAuthoritativeTerminal(current) ? current : null));
        }
      }
      setCommandStatusFailures(0);
      setCommandStatusError(null);
      setActiveLookupState("ready");
    } catch {
      if (signal?.aborted || revision !== activeLookupRevision.current) return;
      setActiveLookupState("failed");
      setActiveLookupError(
        "The authoritative active-command lookup is unavailable. New kill commands are blocked.",
      );
    }
  }, []);

  useEffect(() => {
    return subscribePendingKill((state) => {
      setPendingKill(state);
      if (state.kind === "unavailable") {
        setStorageError(
          "Cross-tab command storage is unavailable or invalid. New kill commands are blocked.",
        );
        return;
      }
      setStorageError(null);
      if (state.kind === "pending") {
        setCommand((current) =>
          current?.commandId === state.commandId ? current : restoredPendingCommand(state),
        );
        void refreshActiveKill();
        return;
      }
      void refreshActiveKill();
    });
  }, [refreshActiveKill]);

  useEffect(() => {
    const controller = new AbortController();
    void refreshActiveKill(controller.signal);
    return () => controller.abort();
  }, [refreshActiveKill]);

  useEffect(() => {
    const resume = () => {
      if (document.visibilityState === "visible") void refreshActiveKill();
    };
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [refreshActiveKill]);

  useEffect(() => {
    if (command === null) return;
    if (isAuthoritativeTerminal(command)) {
      if (!clearPendingKill(command.commandId)) {
        setPendingKill({ kind: "unavailable" });
        setStorageError(
          "The completed command reservation could not be cleared safely. New kill commands are blocked.",
        );
      }
      return;
    }
    if (!persistPendingKill(command.commandId)) {
      setPendingKill({ kind: "unavailable" });
      setStorageError(
        "Pending command identity storage is unavailable. New kill commands are blocked.",
      );
    }
  }, [command]);

  useEffect(() => {
    if (
      command === null ||
      isAuthoritativeTerminal(command) ||
      session.csrfToken === null ||
      commandStatusFailures >= MAX_COMMAND_STATUS_FAILURES
    ) {
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      controlApi
        .killStatus(command.commandId, controller.signal)
        .then((observed) => {
          setCommand(observed);
          setCommandStatusError(null);
          setCommandStatusFailures(0);
        })
        .catch((caught: unknown) => {
          if (!controller.signal.aborted) {
            if (
              caught instanceof ControlApiResponseError &&
              caught.status === 404 &&
              activeLookupState === "ready" &&
              !serverActivePresent
            ) {
              if (clearPendingKill(command.commandId)) {
                setPendingKill({ kind: "clear" });
                setCommand(null);
                setCommandStatusError(null);
                setCommandStatusFailures(0);
              } else {
                setPendingKill({ kind: "unavailable" });
                setStorageError(
                  "The absent command reservation could not be cleared safely. New kill commands are blocked.",
                );
              }
              return;
            }
            setCommandStatusFailures((failures) => {
              const next = failures + 1;
              if (next >= MAX_COMMAND_STATUS_FAILURES) {
                setCommand((current) =>
                  current === null ? null : { ...current, state: "FAILED", authoritative: false },
                );
                setCommandStatusError(
                  "Authoritative status stayed unavailable after bounded checks. The original command identity remains reserved; new submission is blocked.",
                );
              } else {
                setCommandStatusError(
                  "Command status is temporarily unavailable; blind resubmission remains blocked.",
                );
              }
              return next;
            });
          }
        });
    }, COMMAND_POLL_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    activeLookupState,
    command,
    commandStatusFailures,
    serverActivePresent,
    session.csrfToken,
  ]);

  const banner =
    snapshot === null
      ? "UNAVAILABLE"
      : `${snapshot.region.toUpperCase()} · ${snapshot.environment.toUpperCase()} · ${snapshot.mode}`;
  const dangerBanner =
    snapshot === null || snapshot.environment === "prod" || snapshot.mode === "LIVE";
  const commandActive = command !== null && !isAuthoritativeTerminal(command);
  const reconciliation =
    snapshot === null ? null : RECONCILIATION_PRESENTATION[snapshot.reconciliationState];
  const killDisabled =
    session.csrfToken === null ||
    activeLookupState !== "ready" ||
    serverActivePresent ||
    pendingKill.kind !== "clear" ||
    commandActive;

  return (
    <div className="hmt-app-shell">
      <HelpmeTradeHeader
        accountLabel={snapshot?.accountLabel}
        activeView={activeView}
        banner={banner}
        dangerBanner={dangerBanner}
        loading={loading}
        onLogout={onLogout}
        onRefresh={() => void refresh()}
        onViewChange={setActiveView}
        safetyAction={
          <KillSwitchControl
            api={controlApi}
            csrfToken={session.csrfToken ?? ""}
            disabled={killDisabled}
            onObserved={setCommand}
          />
        }
        utc={utc}
      />

      <div className="hmt-workspace">
        <CryptoMarketRail reconciliation={reconciliation} snapshot={snapshot} />

        <main className="hmt-main" id="dashboard-content">
          <div className="hmt-page">
            <section className="hmt-page-heading">
              <div>
                <p className="hmt-page-eyebrow">24×7 crypto operations</p>
                <h1>Account Overview</h1>
                <p>Margin, exposure, P&amp;L and execution safety from reconciled Delta evidence.</p>
              </div>
              <div className="hmt-page-evidence">
                <span>{snapshot?.accountLabel ?? "Account unavailable"}</span>
                <small>
                  Last evidence: {snapshot?.evidenceObservedAt ?? "Unavailable"}
                </small>
              </div>
            </section>

            <section aria-label="Exchange evidence" className="hmt-mobile-evidence">
              <StatusPill
                active={snapshot?.streamConnected === true}
                state={snapshot?.streamConnected === true ? "Connected" : "Unavailable"}
              >
                Market stream
              </StatusPill>
              <StatusPill
                active={snapshot?.catalogueFresh === true}
                state={snapshot?.catalogueFresh === true ? "Fresh" : "Unavailable"}
              >
                Catalogue
              </StatusPill>
              <StatusPill
                active={reconciliation?.ready === true}
                state={reconciliation?.label ?? "Unavailable"}
              >
                Reconciliation
              </StatusPill>
              <StatusPill
                active={snapshot?.riskHalted === false}
                state={snapshot === null ? "Unavailable" : snapshot.riskHalted ? "Halted" : "Open"}
              >
                Risk entries
              </StatusPill>
            </section>

          {snapshot?.mode === "LIVE" ? (
            <div className="live-warning" role="alert">
              LIVE EXECUTION MODE — authenticated commands can affect the selected Delta account.
            </div>
          ) : null}
          {error ? (
            <div className="hmt-alert hmt-alert--danger" role="alert">
              {error}
            </div>
          ) : null}
          {command ? (
            <div
              aria-live="polite"
              className="hmt-alert hmt-alert--warning"
              role="status"
            >
              Kill command {command.commandId} is {command.state}.
              {command.authoritative
                ? " This state was returned by the control API."
                : " Submission outcome is locally uncertain; status reconciliation is active."}
            </div>
          ) : null}
          {commandStatusError ? (
            <div className="hmt-alert hmt-alert--danger" role="alert">
              <p>{commandStatusError}</p>
              {commandStatusFailures >= MAX_COMMAND_STATUS_FAILURES ? (
                <button
                  className="mt-3 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold"
                  onClick={() => {
                    setCommandStatusFailures(0);
                    void refreshActiveKill();
                  }}
                  type="button"
                >
                  Resume status reconciliation
                </button>
              ) : null}
            </div>
          ) : null}
          {activeLookupState === "checking" ? (
            <p aria-live="polite" className="hmt-inline-status" role="status">
              Checking the authoritative active kill command…
            </p>
          ) : null}
          {activeLookupError ? (
            <div className="hmt-alert hmt-alert--danger" role="alert">
              <p>{activeLookupError}</p>
              <button
                className="mt-3 rounded-lg border border-red-300 bg-white px-3 py-2 text-sm font-semibold"
                onClick={() => void refreshActiveKill()}
                type="button"
              >
                Retry active-command lookup
              </button>
            </div>
          ) : null}
          {storageError ? (
            <div className="hmt-alert hmt-alert--danger" role="alert">
              {storageError}
            </div>
          ) : null}

          {activeView === "overview" ? (
            <>
              <AccountSummary snapshot={snapshot} />
              <Positions snapshot={snapshot} />
              <section className="hmt-overview-detail">
                <Orders snapshot={snapshot} />
                <RiskPosture snapshot={snapshot} />
              </section>
            </>
          ) : null}
          {activeView === "positions" ? <Positions snapshot={snapshot} /> : null}
          {activeView === "orders" ? <Orders snapshot={snapshot} /> : null}
          {activeView === "risk" ? <RiskPosture snapshot={snapshot} /> : null}
          </div>
        </main>
        </div>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<OperatorSessionView | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    controlApi
      .session()
      .then(setSession)
      .catch(() => setSession({ authenticated: false, csrfToken: null, absoluteExpiresAt: null }))
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return (
      <main className="grid min-h-screen place-items-center bg-canvas text-slate-600">
        Checking operator session…
      </main>
    );
  }
  if (!session?.authenticated || session.csrfToken === null) {
    return <Login onAuthenticated={setSession} />;
  }
  return (
    <Dashboard
      onLogout={() => {
        void controlApi.logout(session.csrfToken ?? "").finally(() => setSession(null));
      }}
      session={session}
    />
  );
}
