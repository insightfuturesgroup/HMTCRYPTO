export type ExecutionMode = "BACKTEST" | "PAPER" | "LIVE";
export type DeltaRegion = "india" | "global";
export type DeltaEnvironment = "testnet" | "prod";
export type HealthState = "ready" | "degraded" | "failed" | "disabled";
/** Closed wire vocabulary shared with persisted risk evidence. */
export type ReconciliationState = "UNKNOWN" | "BLOCKED" | "READY_CLAIM";
export type ProtectionState =
  | "UNKNOWN"
  | "UNPROTECTED"
  | "VERIFYING"
  | "PAPER_SIMULATED"
  | "NATIVE_VERIFIED"
  | "SYNTHETIC_MONITORING"
  | "STALE";
export type KillEvidenceState = "UNKNOWN" | "RECONCILED_CLAIM";
export type KillCommandState =
  | "QUEUED"
  | "CLAIMED"
  | "SUBMITTING"
  | "UNCERTAIN"
  | "COMPLETED"
  | "FAILED";

export interface ComponentHealth {
  readonly component: string;
  readonly state: HealthState;
  readonly reason: string;
  readonly observedAt: string;
}

export interface HealthSnapshot {
  readonly live: boolean;
  readonly ready: boolean;
  readonly sequence: number;
  readonly observedAt: string;
  readonly components: readonly ComponentHealth[];
}

export interface PositionView {
  readonly productId: number;
  readonly symbol: string;
  readonly side: "long" | "short" | "flat";
  readonly contracts: string;
  readonly entryPrice: string | null;
  readonly lastPrice: string | null;
  readonly markPrice: string | null;
  readonly leverage: string | null;
  readonly liquidationPrice: string | null;
  readonly unrealisedPnl: string | null;
  readonly realisedPnl: string | null;
  readonly marginUsed: string | null;
  readonly fundingRate: string | null;
  readonly protectionState: ProtectionState;
  readonly protectionObservedAt: string | null;
  readonly protectionReconciliationGeneration: number | null;
}

export interface OrderView {
  readonly localOrderId: string;
  readonly exchangeOrderId: string | null;
  readonly clientOrderId: string;
  readonly symbol: string;
  readonly side: "buy" | "sell";
  readonly orderType: string;
  readonly contracts: string;
  readonly limitPrice: string | null;
  readonly status: string;
  readonly reduceOnly: boolean;
  readonly updatedAt: string;
}

export interface DashboardSnapshot {
  readonly sequence: number;
  readonly generatedAt: string;
  readonly evidenceObservedAt: string | null;
  readonly region: DeltaRegion;
  readonly environment: DeltaEnvironment;
  readonly mode: ExecutionMode;
  readonly accountLabel: string;
  readonly catalogueFresh: boolean;
  readonly streamConnected: boolean;
  readonly reconciliationState: ReconciliationState;
  readonly riskHalted: boolean;
  readonly collateralAsset: string | null;
  readonly equity: string | null;
  readonly availableBalance: string | null;
  readonly totalExposure: string | null;
  readonly dailyPnl: string | null;
  readonly marginUsagePercent: string | null;
  readonly positions: readonly PositionView[];
  readonly openOrders: readonly OrderView[];
}

export interface OperatorSessionView {
  readonly authenticated: boolean;
  readonly csrfToken: string | null;
  readonly absoluteExpiresAt: string | null;
}

export interface KillPreview {
  readonly previewId: string;
  readonly expiresAt: string;
  readonly flatten: boolean;
  readonly openOrderCount: number | null;
  readonly positionCount: number | null;
  readonly evidenceState: KillEvidenceState;
  readonly evidenceObservedAt: string | null;
  readonly warning: string;
}

export interface QueuedCommand {
  readonly commandId: string;
  readonly idempotencyKey: string;
  readonly state: KillCommandState;
  readonly acceptedAt: string;
}

export interface KillCommandObservation {
  readonly commandId: string;
  readonly idempotencyKey: string;
  readonly state: KillCommandState;
  readonly acceptedAt: string | null;
  readonly authoritative: boolean;
}

export class ApiContractError extends Error {
  constructor() {
    super("The control API returned an invalid or unsafe response");
    this.name = "ApiContractError";
  }
}
