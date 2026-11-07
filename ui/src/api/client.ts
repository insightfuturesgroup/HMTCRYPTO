import {
  ApiContractError,
  type ComponentHealth,
  type DashboardSnapshot,
  type DeltaEnvironment,
  type DeltaRegion,
  type ExecutionMode,
  type HealthSnapshot,
  type HealthState,
  type KillCommandObservation,
  type KillEvidenceState,
  type KillCommandState,
  type KillPreview,
  type OperatorSessionView,
  type OrderView,
  type PositionView,
  type ProtectionState,
  type QueuedCommand,
  type ReconciliationState,
} from "./contracts";

const MAX_RESPONSE_BYTES = 2_000_000;
const MAX_COLLECTION_ITEMS = 10_000;
const DEFAULT_TIMEOUT_MS = 10_000;
const UTC_INSTANT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?Z$/;
const DECIMAL = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const CONTRACTS = /^(?:0|[1-9]\d*)$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const SAME_ORIGIN_PATH = /^\/(?:[a-z0-9_-]+\/)*[a-z0-9_-]+$/i;
const SAFE_RESPONSE_TOKEN_FIELD = "csrftoken";
const FORBIDDEN_RESPONSE_KEY_PARTS = [
  "apikey",
  "apisecret",
  "secret",
  "signature",
  "authorization",
  "credential",
  "password",
  "cookie",
  "privatekey",
  "deltakey",
  "accesstoken",
  "refreshtoken",
  "bearertoken",
  "sessiontoken",
] as const;

const EXECUTION_MODES = new Set<ExecutionMode>(["BACKTEST", "PAPER", "LIVE"]);
const REGIONS = new Set<DeltaRegion>(["india", "global"]);
const ENVIRONMENTS = new Set<DeltaEnvironment>(["testnet", "prod"]);
const HEALTH_STATES = new Set<HealthState>(["ready", "degraded", "failed", "disabled"]);
const RECONCILIATION_STATES = new Set<ReconciliationState>([
  "UNKNOWN",
  "BLOCKED",
  "READY_CLAIM",
]);
const PROTECTION_STATES = new Set<ProtectionState>([
  "UNKNOWN",
  "UNPROTECTED",
  "VERIFYING",
  "PAPER_SIMULATED",
  "NATIVE_VERIFIED",
  "SYNTHETIC_MONITORING",
  "STALE",
]);
const KILL_EVIDENCE_STATES = new Set<KillEvidenceState>(["UNKNOWN", "RECONCILED_CLAIM"]);
const KILL_STATES = new Set<KillCommandState>([
  "QUEUED",
  "CLAIMED",
  "SUBMITTING",
  "UNCERTAIN",
  "COMPLETED",
  "FAILED",
]);

export class ControlApiResponseError extends Error {
  constructor(readonly status: number) {
    super(`Control API request failed (${status})`);
    this.name = "ControlApiResponseError";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeKey(value: string): string {
  return value.replace(/[^a-z0-9]/gi, "").toLowerCase();
}

function assertSafeShape(value: unknown, depth = 0): void {
  if (depth > 12) throw new ApiContractError();
  if (Array.isArray(value)) {
    if (value.length > MAX_COLLECTION_ITEMS) throw new ApiContractError();
    value.forEach((item) => assertSafeShape(item, depth + 1));
    return;
  }
  if (!isRecord(value)) return;
  const entries = Object.entries(value);
  if (entries.length > 512) throw new ApiContractError();
  for (const [key, child] of entries) {
    const normalized = normalizeKey(key);
    if (
      normalized !== SAFE_RESPONSE_TOKEN_FIELD &&
      FORBIDDEN_RESPONSE_KEY_PARTS.some((part) => normalized.includes(part))
    ) {
      throw new ApiContractError();
    }
    assertSafeShape(child, depth + 1);
  }
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) throw new ApiContractError();
  return value;
}

function requireExactKeys(record: Record<string, unknown>, keys: readonly string[]): void {
  const allowed = new Set(keys);
  if (Object.keys(record).length !== allowed.size) throw new ApiContractError();
  if (Object.keys(record).some((key) => !allowed.has(key))) throw new ApiContractError();
}

function requireString(
  record: Record<string, unknown>,
  key: string,
  { min = 1, max = 4096 }: { readonly min?: number; readonly max?: number } = {},
): string {
  const value = record[key];
  if (typeof value !== "string" || value.length < min || value.length > max) {
    throw new ApiContractError();
  }
  return value;
}

function requireUuid(record: Record<string, unknown>, key: string): string {
  const value = requireString(record, key, { min: 36, max: 36 });
  if (!UUID.test(value)) throw new ApiContractError();
  return value;
}

function requireNullableString(
  record: Record<string, unknown>,
  key: string,
  bounds?: { readonly min?: number; readonly max?: number },
): string | null {
  if (record[key] === null) return null;
  return requireString(record, key, bounds);
}

function requireBoolean(record: Record<string, unknown>, key: string): boolean {
  const value = record[key];
  if (typeof value !== "boolean") throw new ApiContractError();
  return value;
}

function requireInteger(record: Record<string, unknown>, key: string, minimum = 0): number {
  const value = record[key];
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < minimum) {
    throw new ApiContractError();
  }
  return value;
}

function requireNullableInteger(
  record: Record<string, unknown>,
  key: string,
  minimum = 0,
): number | null {
  if (record[key] === null) return null;
  return requireInteger(record, key, minimum);
}

function requireEnum<T extends string>(
  record: Record<string, unknown>,
  key: string,
  values: ReadonlySet<T>,
): T {
  const value = record[key];
  if (typeof value !== "string" || !values.has(value as T)) throw new ApiContractError();
  return value as T;
}

function requireUtcInstant(record: Record<string, unknown>, key: string): string {
  const value = requireString(record, key, { max: 40 });
  const match = UTC_INSTANT.exec(value);
  const instant = new Date(value);
  if (
    match === null ||
    !Number.isFinite(instant.getTime()) ||
    instant.getUTCFullYear() !== Number(match[1]) ||
    instant.getUTCMonth() + 1 !== Number(match[2]) ||
    instant.getUTCDate() !== Number(match[3]) ||
    instant.getUTCHours() !== Number(match[4]) ||
    instant.getUTCMinutes() !== Number(match[5]) ||
    instant.getUTCSeconds() !== Number(match[6])
  ) {
    throw new ApiContractError();
  }
  return value;
}

function requireNullableUtcInstant(record: Record<string, unknown>, key: string): string | null {
  if (record[key] === null) return null;
  return requireUtcInstant(record, key);
}

function requireDecimal(record: Record<string, unknown>, key: string): string | null {
  const value = requireNullableString(record, key, { max: 64 });
  if (value !== null && !DECIMAL.test(value)) throw new ApiContractError();
  return value;
}

function requireContracts(record: Record<string, unknown>, key: string, allowZero: boolean): string {
  const value = requireString(record, key, { max: 64 });
  if (!CONTRACTS.test(value) || (!allowZero && value === "0")) throw new ApiContractError();
  return value;
}

function requireArray(record: Record<string, unknown>, key: string): readonly unknown[] {
  const value = record[key];
  if (!Array.isArray(value) || value.length > MAX_COLLECTION_ITEMS) throw new ApiContractError();
  return value;
}

function parsePosition(value: unknown): PositionView {
  const record = requireRecord(value);
  requireExactKeys(record, [
    "productId",
    "symbol",
    "side",
    "contracts",
    "entryPrice",
    "lastPrice",
    "markPrice",
    "leverage",
    "liquidationPrice",
    "unrealisedPnl",
    "realisedPnl",
    "marginUsed",
    "fundingRate",
    "protectionState",
    "protectionObservedAt",
    "protectionReconciliationGeneration",
  ]);
  return {
    productId: requireInteger(record, "productId", 1),
    symbol: requireString(record, "symbol", { max: 64 }),
    side: requireEnum(record, "side", new Set(["long", "short", "flat"] as const)),
    contracts: requireContracts(record, "contracts", true),
    entryPrice: requireDecimal(record, "entryPrice"),
    lastPrice: requireDecimal(record, "lastPrice"),
    markPrice: requireDecimal(record, "markPrice"),
    leverage: requireDecimal(record, "leverage"),
    liquidationPrice: requireDecimal(record, "liquidationPrice"),
    unrealisedPnl: requireDecimal(record, "unrealisedPnl"),
    realisedPnl: requireDecimal(record, "realisedPnl"),
    marginUsed: requireDecimal(record, "marginUsed"),
    fundingRate: requireDecimal(record, "fundingRate"),
    protectionState: requireEnum(record, "protectionState", PROTECTION_STATES),
    protectionObservedAt: requireNullableUtcInstant(record, "protectionObservedAt"),
    protectionReconciliationGeneration: requireNullableInteger(
      record,
      "protectionReconciliationGeneration",
      1,
    ),
  };
}

function parseOrder(value: unknown): OrderView {
  const record = requireRecord(value);
  requireExactKeys(record, [
    "localOrderId",
    "exchangeOrderId",
    "clientOrderId",
    "symbol",
    "side",
    "orderType",
    "contracts",
    "limitPrice",
    "status",
    "reduceOnly",
    "updatedAt",
  ]);
  return {
    localOrderId: requireUuid(record, "localOrderId"),
    exchangeOrderId: requireNullableString(record, "exchangeOrderId", { max: 128 }),
    clientOrderId: requireString(record, "clientOrderId", { max: 32 }),
    symbol: requireString(record, "symbol", { max: 64 }),
    side: requireEnum(record, "side", new Set(["buy", "sell"] as const)),
    orderType: requireString(record, "orderType", { max: 32 }),
    contracts: requireContracts(record, "contracts", false),
    limitPrice: requireDecimal(record, "limitPrice"),
    status: requireString(record, "status", { max: 32 }),
    reduceOnly: requireBoolean(record, "reduceOnly"),
    updatedAt: requireUtcInstant(record, "updatedAt"),
  };
}

function parseQueuedCommand(value: unknown, expectedId?: string): QueuedCommand {
  const record = requireRecord(value);
  requireExactKeys(record, ["commandId", "state", "acceptedAt"]);
  const commandId = requireUuid(record, "commandId");
  if (expectedId !== undefined && commandId !== expectedId) throw new ApiContractError();
  return {
    commandId,
    idempotencyKey: commandId,
    state: requireEnum(record, "state", KILL_STATES),
    acceptedAt: requireUtcInstant(record, "acceptedAt"),
  };
}

function assertUnique<T>(values: readonly T[]): void {
  if (new Set(values).size !== values.length) throw new ApiContractError();
}

async function readBoundedText(response: Response): Promise<string> {
  if (response.body === null) return response.text();
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let total = 0;
  let text = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new ApiContractError();
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
    return text;
  } catch (error) {
    if (error instanceof ApiContractError) throw error;
    throw new ApiContractError();
  }
}

async function parseJson(
  response: Response,
  allowEmpty = false,
  acceptedStatuses: readonly number[] = [],
): Promise<unknown> {
  if (!response.ok && !acceptedStatuses.includes(response.status)) {
    throw new ControlApiResponseError(response.status);
  }
  if (response.redirected) throw new ApiContractError();
  const length = response.headers.get("content-length");
  if (length !== null) {
    const parsedLength = Number(length);
    if (!Number.isSafeInteger(parsedLength) || parsedLength < 0 || parsedLength > MAX_RESPONSE_BYTES) {
      throw new ApiContractError();
    }
  }
  const text = await readBoundedText(response);
  if (allowEmpty && text.length === 0) return null;
  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("application/json")) throw new ApiContractError();
  let value: unknown;
  try {
    value = JSON.parse(text) as unknown;
  } catch {
    throw new ApiContractError();
  }
  assertSafeShape(value);
  return value;
}

function validateBasePath(basePath: string): void {
  if (!SAME_ORIGIN_PATH.test(basePath)) {
    throw new Error("Control API base path must be a strict same-origin path");
  }
}

function validateRequestPath(path: string): void {
  if (!SAME_ORIGIN_PATH.test(path)) {
    throw new Error("Control API request path is invalid");
  }
}

export class ControlApiClient {
  constructor(
    private readonly basePath = "/api/v1",
    private readonly requestTimeoutMs = DEFAULT_TIMEOUT_MS,
  ) {
    validateBasePath(basePath);
    if (!Number.isSafeInteger(requestTimeoutMs) || requestTimeoutMs < 100 || requestTimeoutMs > 60_000) {
      throw new Error("Control API timeout is invalid");
    }
  }

  private async request(
    path: string,
    init: RequestInit = {},
    {
      allowEmpty = false,
      acceptedStatuses = [],
    }: {
      readonly allowEmpty?: boolean;
      readonly acceptedStatuses?: readonly number[];
    } = {},
  ): Promise<unknown> {
    validateRequestPath(path);
    const controller = new AbortController();
    const propagateAbort = () => controller.abort();
    init.signal?.addEventListener("abort", propagateAbort, { once: true });
    const timeout = globalThis.setTimeout(() => controller.abort(), this.requestTimeoutMs);
    try {
      const response = await fetch(`${this.basePath}${path}`, {
        ...init,
        cache: "no-store",
        credentials: "same-origin",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          ...(init.body ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });
      return await parseJson(response, allowEmpty, acceptedStatuses);
    } finally {
      globalThis.clearTimeout(timeout);
      init.signal?.removeEventListener("abort", propagateAbort);
    }
  }

  async session(): Promise<OperatorSessionView> {
    const record = requireRecord(await this.request("/session"));
    requireExactKeys(record, ["authenticated", "csrfToken", "absoluteExpiresAt"]);
    const authenticated = requireBoolean(record, "authenticated");
    const csrfToken = requireNullableString(record, "csrfToken", { min: 8, max: 4096 });
    const absoluteExpiresAt = requireNullableUtcInstant(record, "absoluteExpiresAt");
    if (authenticated !== (csrfToken !== null && absoluteExpiresAt !== null)) {
      throw new ApiContractError();
    }
    return { authenticated, csrfToken, absoluteExpiresAt };
  }

  async login(password: string): Promise<OperatorSessionView> {
    if (typeof password !== "string" || password.length < 12 || password.length > 1024) {
      throw new Error("Operator password is invalid");
    }
    const record = requireRecord(
      await this.request("/session", {
        method: "POST",
        body: JSON.stringify({ password }),
      }),
    );
    requireExactKeys(record, ["authenticated", "csrfToken", "absoluteExpiresAt"]);
    if (!requireBoolean(record, "authenticated")) throw new ApiContractError();
    return {
      authenticated: true,
      csrfToken: requireString(record, "csrfToken", { min: 8, max: 4096 }),
      absoluteExpiresAt: requireUtcInstant(record, "absoluteExpiresAt"),
    };
  }

  async logout(csrfToken: string): Promise<void> {
    await this.request(
      "/session",
      { method: "DELETE", headers: { "X-CSRF-Token": csrfToken } },
      { allowEmpty: true },
    );
  }

  async health(): Promise<HealthSnapshot> {
    const record = requireRecord(
      await this.request("/health/ready", {}, { acceptedStatuses: [503] }),
    );
    requireExactKeys(record, ["live", "ready", "sequence", "observedAt", "components"]);
    const components = requireArray(record, "components").map((value): ComponentHealth => {
      const component = requireRecord(value);
      requireExactKeys(component, ["component", "state", "reason", "observedAt"]);
      return {
        component: requireString(component, "component", { max: 128 }),
        state: requireEnum(component, "state", HEALTH_STATES),
        reason: requireString(component, "reason", { min: 0, max: 512 }),
        observedAt: requireUtcInstant(component, "observedAt"),
      };
    });
    return {
      live: requireBoolean(record, "live"),
      ready: requireBoolean(record, "ready"),
      sequence: requireInteger(record, "sequence"),
      observedAt: requireUtcInstant(record, "observedAt"),
      components,
    };
  }

  async dashboard(): Promise<DashboardSnapshot> {
    const record = requireRecord(await this.request("/dashboard"));
    requireExactKeys(record, [
      "sequence",
      "generatedAt",
      "evidenceObservedAt",
      "region",
      "environment",
      "mode",
      "accountLabel",
      "catalogueFresh",
      "streamConnected",
      "reconciliationState",
      "riskHalted",
      "collateralAsset",
      "equity",
      "availableBalance",
      "totalExposure",
      "dailyPnl",
      "marginUsagePercent",
      "positions",
      "openOrders",
    ]);
    const positions = requireArray(record, "positions").map(parsePosition);
    const openOrders = requireArray(record, "openOrders").map(parseOrder);
    assertUnique(positions.map((position) => position.productId));
    assertUnique(openOrders.map((order) => order.localOrderId));
    return {
      sequence: requireInteger(record, "sequence"),
      generatedAt: requireUtcInstant(record, "generatedAt"),
      evidenceObservedAt: requireNullableUtcInstant(record, "evidenceObservedAt"),
      region: requireEnum(record, "region", REGIONS),
      environment: requireEnum(record, "environment", ENVIRONMENTS),
      mode: requireEnum(record, "mode", EXECUTION_MODES),
      accountLabel: requireString(record, "accountLabel", { max: 128 }),
      catalogueFresh: requireBoolean(record, "catalogueFresh"),
      streamConnected: requireBoolean(record, "streamConnected"),
      reconciliationState: requireEnum(record, "reconciliationState", RECONCILIATION_STATES),
      riskHalted: requireBoolean(record, "riskHalted"),
      collateralAsset: requireNullableString(record, "collateralAsset", { max: 32 }),
      equity: requireDecimal(record, "equity"),
      availableBalance: requireDecimal(record, "availableBalance"),
      totalExposure: requireDecimal(record, "totalExposure"),
      dailyPnl: requireDecimal(record, "dailyPnl"),
      marginUsagePercent: requireDecimal(record, "marginUsagePercent"),
      positions,
      openOrders,
    };
  }

  async previewKill(flatten: boolean, csrfToken: string, signal?: AbortSignal): Promise<KillPreview> {
    const record = requireRecord(
      await this.request("/commands/kill/preview", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken },
        body: JSON.stringify({ flatten }),
        signal,
      }),
    );
    requireExactKeys(record, [
      "previewId",
      "expiresAt",
      "flatten",
      "openOrderCount",
      "positionCount",
      "evidenceState",
      "evidenceObservedAt",
      "warning",
    ]);
    const authoritativeFlatten = requireBoolean(record, "flatten");
    if (authoritativeFlatten !== flatten) throw new ApiContractError();
    return {
      previewId: requireUuid(record, "previewId"),
      expiresAt: requireUtcInstant(record, "expiresAt"),
      flatten: authoritativeFlatten,
      openOrderCount: requireNullableInteger(record, "openOrderCount"),
      positionCount: requireNullableInteger(record, "positionCount"),
      evidenceState: requireEnum(record, "evidenceState", KILL_EVIDENCE_STATES),
      evidenceObservedAt: requireNullableUtcInstant(record, "evidenceObservedAt"),
      warning: requireString(record, "warning", { max: 1024 }),
    };
  }

  async engageKill(
    previewId: string,
    flatten: boolean,
    confirmation: string | null,
    csrfToken: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ): Promise<QueuedCommand> {
    if (!UUID.test(previewId) || !UUID.test(idempotencyKey)) throw new ApiContractError();
    return parseQueuedCommand(
      await this.request("/commands/kill", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken, "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ previewId, flatten, confirmation }),
        signal,
      }),
      idempotencyKey,
    );
  }

  async killStatus(
    commandId: string,
    signal?: AbortSignal,
  ): Promise<KillCommandObservation> {
    if (!UUID.test(commandId)) throw new ApiContractError();
    const command = parseQueuedCommand(
      await this.request(`/commands/${commandId}`, { method: "GET", signal }),
      commandId,
    );
    return {
      commandId: command.commandId,
      idempotencyKey: commandId,
      state: command.state,
      acceptedAt: command.acceptedAt,
      authoritative: true,
    };
  }

  async activeKill(signal?: AbortSignal): Promise<KillCommandObservation | null> {
    const record = requireRecord(
      await this.request("/commands/kill/active", { method: "GET", signal }),
    );
    requireExactKeys(record, ["active"]);
    if (record.active === null) return null;
    const command = parseQueuedCommand(record.active);
    if (command.state === "COMPLETED" || command.state === "FAILED") throw new ApiContractError();
    return { ...command, authoritative: true };
  }
}

export const controlApi = new ControlApiClient();
