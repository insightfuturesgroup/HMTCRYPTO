import { type ReconciliationState } from "../api/contracts";

export interface ReconciliationPresentation {
  readonly label: string;
  readonly ready: boolean;
}

/** Exhaustive UI projection of the persistence/API reconciliation vocabulary. */
export const RECONCILIATION_PRESENTATION: Readonly<
  Record<ReconciliationState, ReconciliationPresentation>
> = {
  UNKNOWN: { label: "Unknown", ready: false },
  BLOCKED: { label: "Blocked", ready: false },
  READY_CLAIM: { label: "Current", ready: true },
};
