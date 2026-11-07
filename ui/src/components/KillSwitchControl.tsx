import { AlertTriangle, ShieldAlert, X } from "lucide-react";
import { type KeyboardEvent, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ControlApiResponseError, type ControlApiClient } from "../api/client";
import { type KillCommandObservation, type KillPreview } from "../api/contracts";
import { clearPendingKill, persistPendingKill } from "../lib/pendingKill";

interface Props {
  readonly api: ControlApiClient;
  readonly csrfToken: string;
  readonly disabled?: boolean;
  readonly onObserved: (command: KillCommandObservation) => void;
}

const FLATTEN_CONFIRMATION = "FLATTEN_ALL_POSITIONS";
const FOCUSABLE = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

type Operation = "idle" | "preview" | "engage";

export function KillSwitchControl({ api, csrfToken, disabled, onObserved }: Props) {
  const [open, setOpen] = useState(false);
  const [flatten, setFlatten] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [preview, setPreview] = useState<KillPreview | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const [operation, setOperation] = useState<Operation>("idle");
  const [ambiguous, setAmbiguous] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const activeController = useRef<AbortController | null>(null);
  const operationRevision = useRef(0);
  const trackedKeys = useRef(new Set<string>());
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const initialFocusRef = useRef<HTMLButtonElement | null>(null);
  const confirmationRef = useRef<HTMLInputElement | null>(null);

  const busy = operation !== "idle";
  const previewExpired = preview !== null && Date.parse(preview.expiresAt) <= nowMs;

  useEffect(() => {
    if (preview === null) return;
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [preview]);

  useEffect(() => {
    if (!open) return;
    const root = document.getElementById("root");
    const rootWasInert = root?.hasAttribute("inert") ?? false;
    const previousOverflow = document.body.style.overflow;
    root?.setAttribute("inert", "");
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => initialFocusRef.current?.focus(), 0);
    return () => {
      window.clearTimeout(focusTimer);
      if (!rootWasInert) root?.removeAttribute("inert");
      document.body.style.overflow = previousOverflow;
      triggerRef.current?.focus();
    };
  }, [open]);

  useEffect(() => {
    if (preview === null) return;
    const focusTimer = window.setTimeout(() => {
      if (preview.flatten) confirmationRef.current?.focus();
      else dialogRef.current?.querySelector<HTMLButtonElement>("[data-engage]")?.focus();
    }, 0);
    return () => window.clearTimeout(focusTimer);
  }, [preview]);

  function publishUncertain(key: string | null): void {
    if (key === null || trackedKeys.current.has(key)) return;
    trackedKeys.current.add(key);
    onObserved({
      commandId: key,
      idempotencyKey: key,
      state: "UNCERTAIN",
      acceptedAt: null,
      authoritative: false,
    });
  }

  function clearDialogState(): void {
    setPreview(null);
    setIdempotencyKey(null);
    setConfirmation("");
    setAmbiguous(false);
    setError(null);
    setOperation("idle");
  }

  function close(): void {
    operationRevision.current += 1;
    if (operation === "engage") publishUncertain(idempotencyKey);
    activeController.current?.abort();
    activeController.current = null;
    setOpen(false);
    clearDialogState();
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLElement>): void {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [],
    ).filter((element) => !element.hasAttribute("disabled"));
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }

  async function createPreview(): Promise<void> {
    const revision = operationRevision.current + 1;
    operationRevision.current = revision;
    const controller = new AbortController();
    activeController.current = controller;
    setOperation("preview");
    setError(null);
    try {
      const verified = await api.previewKill(flatten, csrfToken, controller.signal);
      if (operationRevision.current !== revision) return;
      const key = crypto.randomUUID();
      setPreview(verified);
      setIdempotencyKey(key);
    } catch {
      if (operationRevision.current === revision && !controller.signal.aborted) {
        setError("Kill preview could not be verified. No command was queued.");
      }
    } finally {
      if (operationRevision.current === revision) {
        activeController.current = null;
        setOperation("idle");
      }
    }
  }

  function discardPreview(): void {
    setPreview(null);
    setIdempotencyKey(null);
    setConfirmation("");
    setAmbiguous(false);
    setError(null);
  }

  async function engage(): Promise<void> {
    if (preview === null || idempotencyKey === null) return;
    if (preview.flatten !== flatten) {
      setError("Preview mode no longer matches the requested action. Create a new preview.");
      return;
    }
    if (Date.parse(preview.expiresAt) <= Date.now()) {
      setError("The safety preview expired. Create a new preview before engaging.");
      return;
    }
    if (flatten && confirmation !== FLATTEN_CONFIRMATION) {
      setError("Enter the exact flatten confirmation phrase.");
      return;
    }
    if (!persistPendingKill(idempotencyKey)) {
      setError("The command identity could not be persisted safely. No command was submitted.");
      return;
    }
    const revision = operationRevision.current + 1;
    operationRevision.current = revision;
    const controller = new AbortController();
    activeController.current = controller;
    setOperation("engage");
    setError(null);
    try {
      const command = await api.engageKill(
        preview.previewId,
        preview.flatten,
        preview.flatten ? confirmation : null,
        csrfToken,
        idempotencyKey,
        controller.signal,
      );
      if (operationRevision.current !== revision) return;
      trackedKeys.current.add(idempotencyKey);
      onObserved({ ...command, authoritative: true });
      setOpen(false);
      clearDialogState();
    } catch (caught) {
      const definitelyRejected =
        caught instanceof ControlApiResponseError &&
        caught.status >= 400 &&
        caught.status < 500 &&
        caught.status !== 408;
      let reservationCleared = false;
      if (definitelyRejected) {
        reservationCleared = clearPendingKill(idempotencyKey);
      } else {
        publishUncertain(idempotencyKey);
      }
      if (operationRevision.current === revision) {
        if (definitelyRejected) {
          setPreview(null);
          setIdempotencyKey(null);
          setConfirmation("");
          setError(
            reservationCleared
              ? "The control API rejected the command before admission. Create a new preview."
              : "The command was rejected, but its cross-tab reservation could not be cleared safely. New submissions remain blocked.",
          );
        } else {
          setAmbiguous(true);
          setError(
            "Submission outcome is unknown. Blind retry is blocked while command status is reconciled.",
          );
        }
      }
    } finally {
      if (operationRevision.current === revision) {
        activeController.current = null;
        setOperation("idle");
      }
    }
  }

  const modal = open ? (
    <div className="kill-dialog-overlay" role="presentation">
      <div className="grid min-h-full place-items-center">
        <section
          aria-describedby="kill-description"
          aria-labelledby="kill-title"
          aria-modal="true"
          className="kill-dialog"
          onKeyDown={handleDialogKeyDown}
          ref={dialogRef}
          role="dialog"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex gap-3">
              <span aria-hidden="true" className="kill-dialog__icon">
                <AlertTriangle />
              </span>
              <div>
                <h2 className="kill-dialog__title" id="kill-title">
                  Global kill switch
                </h2>
                <p className="kill-dialog__description" id="kill-description">
                  This always halts new risk and cancels risk-increasing orders first.
                </p>
              </div>
            </div>
            <button
              aria-label="Close kill switch dialog"
              className="kill-dialog__close"
              onClick={close}
              ref={initialFocusRef}
              type="button"
            >
              <X />
            </button>
          </div>

          <label className="kill-dialog__option">
            <input
              checked={flatten}
              className="mt-1 h-4 w-4 accent-red-600"
              disabled={preview !== null || busy}
              onChange={(event) => setFlatten(event.target.checked)}
              type="checkbox"
            />
            <span>
              <span className="block font-semibold text-slate-900">Also flatten positions</span>
              <span className="block text-sm text-slate-600">
                Uses reduce-only actions and requires final exchange reconciliation.
              </span>
            </span>
          </label>

          {operation === "preview" ? (
            <p aria-live="polite" className="mt-5 text-center text-sm font-medium" role="status">
              Checking available reconciled evidence…
            </p>
          ) : null}

          {preview === null ? (
            <button
              className="kill-dialog__primary"
              disabled={busy}
              onClick={() => void createPreview()}
              type="button"
            >
              Create safety preview
            </button>
          ) : (
            <div className="mt-5 space-y-4">
              <div className="kill-dialog__preview">
                <p className="font-semibold">{preview.warning}</p>
                <p className="mt-2">
                  Mode: {preview.flatten ? "cancel and flatten" : "cancel only"} · Open orders:{" "}
                  {preview.openOrderCount ?? "Unknown"} · Positions:{" "}
                  {preview.positionCount ?? "Unknown"}
                </p>
                <p className="mt-1">
                  Evidence: {preview.evidenceState}
                  {preview.evidenceObservedAt === null
                    ? " (timestamp unavailable)"
                    : ` at ${preview.evidenceObservedAt}`}
                </p>
                <p className="mt-1">Preview expires {preview.expiresAt}</p>
              </div>
              {previewExpired ? (
                <div className="rounded-lg bg-red-50 p-3 text-sm font-medium text-red-700" role="alert">
                  This preview has expired and cannot be submitted.
                </div>
              ) : null}
              {flatten ? (
                <label className="block text-sm font-medium text-slate-800" htmlFor="flatten-confirmation">
                  Type <code className="rounded bg-slate-100 px-1 py-0.5">{FLATTEN_CONFIRMATION}</code>
                  <input
                    autoComplete="off"
                    className="mt-2 w-full rounded-lg border border-slate-300 px-3 py-2 font-mono text-sm outline-none focus:border-red-500 focus:ring-2 focus:ring-red-100"
                    disabled={ambiguous || previewExpired}
                    id="flatten-confirmation"
                    onChange={(event) => setConfirmation(event.target.value)}
                    ref={confirmationRef}
                    value={confirmation}
                  />
                </label>
              ) : null}
              {ambiguous ? (
                <button
                  className="kill-dialog__primary"
                  onClick={close}
                  type="button"
                >
                  Close and monitor status
                </button>
              ) : previewExpired ? (
                <button
                  className="kill-dialog__primary"
                  onClick={discardPreview}
                  type="button"
                >
                  Discard expired preview
                </button>
              ) : (
                <button
                  className="kill-dialog__engage"
                  data-engage="true"
                  disabled={busy || (flatten && confirmation !== FLATTEN_CONFIRMATION)}
                  onClick={() => void engage()}
                  type="button"
                >
                  {operation === "engage" ? "Submitting once…" : "ENGAGE KILL SWITCH"}
                </button>
              )}
            </div>
          )}
          {error ? (
            <p className="mt-4 rounded-lg bg-red-50 p-3 text-sm font-medium text-red-700" role="alert">
              {error}
            </p>
          ) : null}
        </section>
      </div>
    </div>
  ) : null;

  return (
    <>
      <button
        aria-disabled={disabled}
        className={`kill-switch-trigger ${
          disabled
            ? "kill-switch-trigger--disabled"
            : "kill-switch-trigger--ready"
        }`}
        onClick={() => {
          if (!disabled) setOpen(true);
        }}
        ref={triggerRef}
        type="button"
      >
        <ShieldAlert aria-hidden="true" size={17} /> Kill switch
      </button>
      {modal === null ? null : createPortal(modal, document.body)}
    </>
  );
}
