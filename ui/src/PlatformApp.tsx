import { FormEvent, useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  Activity,
  Bell,
  BookOpen,
  BriefcaseBusiness,
  ChevronDown,
  Copy,
  Info,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Menu,
  MoreVertical,
  Pause,
  Pencil,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Target,
  Trash2,
  Users,
  X,
} from "lucide-react";
import {
  invalidateApplicationQueries,
  queryClient,
  queryStaleTime,
} from "./queryClient";

type Role = "admin" | "trader" | "viewer";
type Page =
  | "dashboard"
  | "accounts"
  | "orders"
  | "positions"
  | "gtt"
  | "strategies"
  | "tradingview"
  | "risk"
  | "performance"
  | "notifications"
  | "settings"
  | "users"
  | "audit";

interface SessionUser {
  id: string;
  email: string;
  fullName: string;
  role: Role;
}

interface Session {
  authenticated: boolean;
  csrfToken: string | null;
  readOnlyMode: boolean;
  user: SessionUser | null;
}

interface Account {
  id: string;
  profileName: string;
  environment: "testnet" | "prod";
  mode: "LIVE";
  permission: "view" | "trade" | "manage";
  identityVerified: boolean;
  credentialConfigured: boolean;
  credentialState: "unconfigured" | "pending" | "verified" | "failed";
  credentialVersion?: number | null;
  reconciliationState: "unknown" | "running" | "ready" | "degraded" | "blocked";
  reconciledAt?: string | null;
  leaseActive?: boolean;
  leaseUntil?: string | null;
  fresh?: boolean;
  observedAt?: string | null;
  activeProtectionCount?: number;
  entriesEnabled: boolean;
  exitsEnabled: boolean;
}

interface Product {
  productId: number;
  symbol: string;
  contractType: string;
  state: string;
  onlyReduceOnlyOrdersAllowed: boolean;
  contractValue: string;
  contractUnitCurrency: string;
  tickSize: string;
  initialMargin: string | null;
  maintenanceMargin: string | null;
  defaultLeverage: string | null;
  tradingStatus: string;
  markPrice: string | null;
  bestBid: string | null;
  bestAsk: string | null;
  observedAt: string | null;
  quoteFresh: boolean;
}

interface RiskPolicyView {
  version?: number;
  maxOrderNotional: string;
  maxPositionNotional: string;
  allowedProductIds: number[];
  enabled: boolean;
  qualified?: boolean;
  qualificationReason?: string;
  qualificationWindowHours?: number;
}

interface WatchlistRow {
  id: string;
  symbol: string;
  productId: number;
}

interface WatchlistGroupRow {
  id: string;
  name: string;
  position: number;
  items: (WatchlistRow & { position: number })[];
}

interface DashboardOverview {
  observedAt: string;
  fresh: boolean;
  totals: {
    availableBalance: string;
    walletBalance: string;
    usedMargin: string;
    unrealizedPnl: string;
    positions: number;
    openOrders: number;
    activeGtt: number;
  };
  accounts: {
    accountId: string;
    profileName: string;
    availableBalance: string;
    walletBalance: string;
    usedMargin: string;
    unrealizedPnl: string;
    positionCount: number;
    openOrderCount: number;
    activeGttCount: number;
    fresh: boolean;
    observedAt: string | null;
  }[];
}

interface CandleRow {
  timestamp: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string | null;
  observedAt: string;
}

interface UserRow extends SessionUser {
  status: "invited" | "active" | "disabled";
  createdAt: string;
  lastLoginAt: string | null;
}

interface AccountAssignmentRow {
  id: string;
  accountId: string;
  accountName: string;
  userId: string;
  userEmail: string;
  permission: "view" | "trade" | "manage";
  updatedAt: string;
}

interface AuditRow {
  id: string;
  action: string;
  targetType: string;
  targetId: string;
  outcome: string;
  occurredAt: string;
}

interface HealthAccount {
  accountId: string;
  profileName: string;
  credentialState: string;
  reconciliationState: string;
  reconciliationAgeSeconds: number | null;
  workerLeaseActive: boolean;
  riskConfigured: boolean;
  riskEnabled: boolean;
  entriesEnabled: boolean;
  exitsEnabled: boolean;
  lastErrorCode: string | null;
}

interface AccountState {
  accountId: string;
  observedAt: string;
  reconciledAt: string;
  freshUntil: string;
  ageSeconds: number;
  fresh: boolean;
  reconciliationState: string;
  leaseActive: boolean;
  leaseUntil: string | null;
  credentialVersion: number;
  balances: Record<string, unknown>[];
  positions: Record<string, unknown>[];
  openOrders: Record<string, unknown>[];
  fills: Record<string, unknown>[];
  walletTransactions: Record<string, unknown>[];
}

interface LocalOrderIntent {
  intentId: string;
  productId: number;
  symbol: string;
  side: string;
  contracts: number;
  orderType: string;
  price: string;
  intentStatus: string;
  submissionStatus: string;
  exchangeOrderId: number | null;
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
}

interface OrderPreview {
  previewId: string;
  accountId: string;
  productId: number;
  price: string;
  priceBasis: string;
  notional: string;
  projectedExposure?: string;
  riskHeadroom?: string;
  quoteAgeSeconds?: number;
  reconciliationAgeSeconds?: number;
  requiredProtection?: {
    stopLossPct: string;
    targetPct: string;
    trailingOffsetPct: string;
  };
  expiresAt: string;
}

interface GttRow {
  id: string;
  productId: number;
  symbol: string;
  side: "buy" | "sell";
  contracts: number;
  triggerKind: "stop_loss" | "target" | "trailing_stop";
  triggerPrice: string | null;
  trailOffset: string | null;
  limitPrice: string | null;
  status: string;
  updatedAt: string;
}

interface TradingViewStrategyRow {
  id: string;
  accountId: string;
  name: string;
  productId: number;
  symbol: string;
  contracts: number;
  orderType: "market" | "limit";
  reduceOnly: boolean;
  stopLossPct: string;
  targetPct: string;
  trailingOffsetPct: string;
  protectionRequired: boolean;
  enabled: boolean;
  webhookConfigured: boolean;
  webhookFingerprint: string | null;
  updatedAt: string;
}

interface TradingViewSignalRow {
  id: string;
  strategyName: string;
  externalSignalId: string;
  side: "buy" | "sell";
  limitPrice: string | null;
  status: string;
  rejectionCode: string | null;
  intentId: string | null;
  receivedAt: string;
  processedAt: string | null;
}

interface LiveOrderActionRow {
  id: string;
  action: "cancel" | "modify";
  orderId: number;
  productId: number;
  targetSize: number | null;
  targetLimitPrice: string | null;
  status: string;
  lastErrorCode: string | null;
  createdAt: string;
  resolvedAt: string | null;
}

interface PerformanceSummary {
  accountId: string;
  observedAt: string;
  fresh: boolean;
  fillCount: number;
  netWalletChange: string;
  funding: string;
  commission: string;
  liquidationFees: string;
  settlement: string;
  totals: Record<string, string>;
}

interface NotificationRow {
  id: string;
  accountName: string;
  severity: "critical" | "warning";
  code: string;
  message: string;
  observedAt: string;
  firstObservedAt: string;
  acknowledgedAt: string | null;
}

interface GeneralSettings {
  venue: string;
  region: string;
  environment: string;
  mode: string;
  operationsContactEmail: string;
  displayTimezone: "UTC" | "Asia/Kolkata";
  editable: boolean;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  csrf?: string,
): Promise<T> {
  const method = init?.method?.toUpperCase() ?? "GET";
  if (method === "GET" && path !== "/api/v1/auth/session") {
    return queryClient.fetchQuery({
      queryKey: [path],
      queryFn: ({ signal }) =>
        networkRequest<T>(path, { ...init, signal }, csrf),
      staleTime: queryStaleTime(path),
    });
  }
  const value = await networkRequest<T>(path, init, csrf);
  if (!["GET", "HEAD"].includes(method)) await invalidateApplicationQueries();
  return value;
}

async function networkRequest<T>(
  path: string,
  init?: RequestInit,
  csrf?: string,
): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body !== undefined) headers.set("Content-Type", "application/json");
  if (csrf !== undefined) headers.set("X-CSRF-Token", csrf);
  if (init?.method !== undefined && !["GET", "HEAD"].includes(init.method)) {
    headers.set("Idempotency-Key", crypto.randomUUID());
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (!response.ok) {
    let code = "request_failed";
    try {
      const body = (await response.json()) as {
        detail?: unknown;
        error?: unknown;
      };
      const candidate = body.detail ?? body.error;
      if (typeof candidate === "string" && /^[a-z0-9_]{1,64}$/.test(candidate))
        code = candidate;
      else if (Array.isArray(candidate))
        code = "validation_error";
      else if (candidate && typeof candidate === "object") {
        const detailCode = (candidate as { code?: unknown }).code;
        if (typeof detailCode === "string" && /^[a-z0-9_]{1,64}$/.test(detailCode))
          code = detailCode;
      }
    } catch {
      /* Safe stable fallback; never render server diagnostics. */
    }
    throw new RequestError(response.status, code);
  }
  return (await response.json()) as T;
}

class RequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super(code);
    this.name = "RequestError";
  }
}

function Login({ onLogin }: { readonly onLogin: (session: Session) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(
        await request<Session>("/api/v1/auth/login", {
          method: "POST",
          body: JSON.stringify({ email, password }),
        }),
      );
    } catch {
      setError("Authentication failed or this user is not active.");
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <main className="hmt-auth">
      <section className="hmt-auth-card">
        <div className="hmt-auth-brand">
          <span className="hmt-auth-mark">
            <ShieldCheck />
          </span>
          <div>
            <h1>HMT Crypto</h1>
            <p>Delta Exchange India · Live control</p>
          </div>
        </div>
        <div className="hmt-auth-notice">
          Invitation-only access. Live entries remain disabled until account
          qualification.
        </div>
        <form
          className="hmt-auth-form"
          onSubmit={(event) => void submit(event)}
        >
          <label>
            Email
            <input
              autoComplete="username"
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
              value={email}
            />
          </label>
          <label className="mt-4">
            Password
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          <button className="hmt-auth-submit" disabled={busy} type="submit">
            {busy ? "Signing in…" : "Sign in"}
          </button>
          {error ? (
            <p className="hmt-auth-error" role="alert">
              {error}
            </p>
          ) : null}
        </form>
        <a
          className="mt-5 block text-center text-xs text-slate-500"
          href="/operator"
        >
          Emergency operator access
        </a>
      </section>
    </main>
  );
}

function PasswordRecovery({
  accessToken,
  onComplete,
}: {
  readonly accessToken: string;
  readonly onComplete: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await request("/api/v1/auth/password", {
        method: "POST",
        body: JSON.stringify({
          access_token: accessToken,
          new_password: password,
        }),
      });
      window.history.replaceState(
        null,
        "",
        window.location.pathname + window.location.search,
      );
      onComplete();
    } catch {
      setError("The recovery link is invalid or expired.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="hmt-auth-page">
      <section className="hmt-auth-card">
        <div className="hmt-auth-brand">
          <span>HMT</span>
          <strong>CRYPTO</strong>
        </div>
        <h1>Set a new password</h1>
        <p>
          This recovery link is used once. Existing application sessions will be
          revoked.
        </p>
        <form onSubmit={(event) => void submit(event)}>
          <label>
            New password
            <input
              autoComplete="new-password"
              minLength={12}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          <label>
            Confirm password
            <input
              autoComplete="new-password"
              minLength={12}
              onChange={(event) => setConfirmation(event.target.value)}
              required
              type="password"
              value={confirmation}
            />
          </label>
          <button className="hmt-auth-submit" disabled={busy} type="submit">
            {busy ? "Updating…" : "Update password"}
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

const navigation: readonly {
  page: Page;
  path: string;
  label: string;
  icon: typeof Activity;
  admin?: boolean;
}[] = [
  {
    page: "dashboard",
    path: "/dashboard",
    label: "Dashboard",
    icon: LayoutDashboard,
  },
  {
    page: "accounts",
    path: "/accounts",
    label: "Delta Accounts",
    icon: KeyRound,
  },
  {
    page: "orders",
    path: "/orders",
    label: "Orders",
    icon: ListChecks,
  },
  {
    page: "positions",
    path: "/positions",
    label: "Positions",
    icon: BriefcaseBusiness,
  },
  { page: "gtt", path: "/gtt", label: "GTT", icon: Target },
  { page: "strategies", path: "/strategies", label: "Strategies", icon: Settings },
  {
    page: "tradingview",
    path: "/tradingview",
    label: "TradingView",
    icon: BookOpen,
  },
  { page: "risk", path: "/risk", label: "Risk", icon: ShieldCheck },
  {
    page: "performance",
    path: "/performance",
    label: "Performance",
    icon: Activity,
  },
  {
    page: "notifications",
    path: "/notifications",
    label: "Notifications",
    icon: Bell,
  },
  { page: "settings", path: "/settings", label: "Settings", icon: Settings },
  {
    page: "users",
    path: "/users",
    label: "User Management",
    icon: Users,
    admin: true,
  },
  {
    page: "audit",
    path: "/health",
    label: "Health & Audit",
    icon: ShieldCheck,
    admin: true,
  },
];

const desktopNavigation = [
  { label: "Dashboard", path: "/dashboard" },
  { label: "Delta Accounts", path: "/accounts" },
  { label: "Orders", path: "/orders" },
  { label: "Positions", path: "/positions" },
  { label: "GTT", path: "/gtt" },
  {
    label: "Trading",
    items: [
      { label: "Strategies", path: "/strategies" },
      { label: "TradingView", path: "/tradingview" },
    ],
  },
  {
    label: "Reports",
    items: [
      { label: "Performance & Fees", path: "/performance" },
      { label: "Notifications", path: "/notifications" },
    ],
  },
  {
    label: "Settings",
    items: [
      { label: "Risk Management", path: "/risk" },
      { label: "General Settings", path: "/settings" },
    ],
  },
  {
    label: "Administration",
    admin: true,
    items: [
      { label: "User Management", path: "/users" },
      { label: "Health & Audit", path: "/health" },
    ],
  },
] as const;

function Panel({
  title,
  children,
}: {
  readonly title: string;
  readonly children: React.ReactNode;
}) {
  return (
    <section className="data-panel">
      <div className="data-panel__header">
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function money(value: string, currency = "USD") {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value || "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(parsed);
}

function StatusBadge({
  value,
  good,
}: {
  readonly value: string;
  readonly good?: boolean;
}) {
  return (
    <span className={`status-pill ${good ? "status-pill--good" : ""}`}>
      <span className="status-dot" />
      {value.replaceAll("_", " ")}
    </span>
  );
}

function CryptoMarketRail({
  csrf,
  open,
  onClose,
}: {
  readonly csrf: string;
  readonly open: boolean;
  readonly onClose: () => void;
}) {
  const [products, setProducts] = useState<Product[]>([]);
  const [groups, setGroups] = useState<WatchlistGroupRow[]>([]);
  const [activeGroup, setActiveGroup] = useState("");
  const [search, setSearch] = useState("");

  const load = useCallback(() => {
    void Promise.all([
      request<{ products: Product[] }>("/api/v1/market/products"),
      request<{ groups: WatchlistGroupRow[] }>("/api/v1/market/watchlists"),
    ]).then(([market, saved]) => {
      setProducts(market.products);
      setGroups(saved.groups);
      setActiveGroup((current) => current || saved.groups[0]?.id || "");
    });
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    const stream = new EventSource("/api/v1/market/stream", {
      withCredentials: true,
    });
    const receive = (event: MessageEvent<string>) => {
      try {
        const value = JSON.parse(event.data) as { products: Product[] };
        setProducts(value.products);
      } catch {
        /* A malformed frame is ignored; the stream reconnects automatically. */
      }
    };
    stream.addEventListener("market", receive as EventListener);
    return () => stream.close();
  }, []);
  useEffect(() => {
    // Low-frequency recovery only. Normal quote delivery is server-pushed.
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);
  useEffect(() => {
    const refreshVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    return () => document.removeEventListener("visibilitychange", refreshVisible);
  }, [load]);

  const group = groups.find((item) => item.id === activeGroup) ?? groups[0];
  const savedSymbols = new Set(group?.items.map((item) => item.symbol) ?? []);
  const featured = products.filter((item) =>
    ["BTCUSD", "ETHUSD"].includes(item.symbol),
  );
  const visibleProducts = search.trim()
    ? products
        .filter((item) =>
          item.symbol.toLowerCase().includes(search.trim().toLowerCase()),
        )
        .slice(0, 20)
    : products.filter((item) => savedSymbols.has(item.symbol));

  async function toggle(product: Product) {
    if (!group) return;
    const existing = group.items.find(
      (item) => item.productId === product.productId,
    );
    if (existing)
      await request(
        `/api/v1/market/watchlists/${group.id}/items/${existing.id}`,
        { method: "DELETE" },
        csrf,
      );
    else
      await request(
        `/api/v1/market/watchlists/${group.id}/items`,
        {
          method: "PUT",
          body: JSON.stringify({
            symbol: product.symbol,
            product_id: product.productId,
          }),
        },
        csrf,
      );
    load();
  }

  async function createGroup() {
    const name = window.prompt("Watchlist name", `Watchlist ${groups.length + 1}`)?.trim();
    if (!name) return;
    const created = await request<{ id: string }>(
      "/api/v1/market/watchlists",
      { method: "POST", body: JSON.stringify({ name }) },
      csrf,
    );
    setActiveGroup(created.id);
    load();
  }

  async function renameGroup() {
    if (!group) return;
    const name = window.prompt("Rename watchlist", group.name)?.trim();
    if (!name || name === group.name) return;
    await request(
      `/api/v1/market/watchlists/${group.id}`,
      { method: "PATCH", body: JSON.stringify({ name }) },
      csrf,
    );
    load();
  }

  async function deleteGroup() {
    if (!group || groups.length === 1 || !window.confirm(`Delete ${group.name}?`)) return;
    await request(`/api/v1/market/watchlists/${group.id}`, { method: "DELETE" }, csrf);
    setActiveGroup("");
    load();
  }

  return (
    <aside className={`market-rail ${open ? "is-open" : ""}`}>
      <div className="market-rail__mobile-heading">
        <strong>Crypto watchlist</strong>
        <button aria-label="Close watchlist" onClick={onClose} type="button">
          <X size={20} />
        </button>
      </div>
      <div className="market-rail__indices">
        {featured.map((product) => (
          <div key={product.productId}>
            <small>{product.symbol}</small>
            <strong>{product.markPrice ?? "—"}</strong>
            <span className={product.quoteFresh ? "is-fresh" : "is-stale"}>
              {product.quoteFresh ? "Fresh" : "Stale"}
            </span>
          </div>
        ))}
      </div>
      <label className="market-rail__search">
        <Search size={16} />
        <input
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search crypto contracts"
          value={search}
        />
        <kbd>Ctrl K</kbd>
      </label>
      <div className="market-rail__groupbar">
        <select
          aria-label="Watchlist group"
          onChange={(event) => setActiveGroup(event.target.value)}
          value={group?.id ?? ""}
        >
          {groups.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name} ({item.items.length})
            </option>
          ))}
        </select>
        <div className="market-rail__group-actions">
          <button aria-label="New watchlist" onClick={() => void createGroup()} title="New watchlist" type="button"><Plus size={15} /></button>
          <button aria-label="Rename watchlist" onClick={() => void renameGroup()} title="Rename watchlist" type="button"><Pencil size={14} /></button>
          <button aria-label="Delete watchlist" disabled={groups.length === 1} onClick={() => void deleteGroup()} title="Delete watchlist" type="button"><Trash2 size={14} /></button>
        </div>
      </div>
      <div className="market-rail__list">
        {visibleProducts.map((product) => (
          <article className="market-rail__contract" key={product.productId}>
            <div>
              <strong>{product.symbol}</strong>
              <small>{product.contractType.replaceAll("_", " ")}</small>
            </div>
            <button
              aria-label={`${savedSymbols.has(product.symbol) ? "Remove" : "Add"} ${product.symbol}`}
              onClick={() => void toggle(product)}
              type="button"
            >
              {savedSymbols.has(product.symbol) ? "−" : "+"}
            </button>
            <dl>
              <div><dt>Bid</dt><dd>{product.bestBid ?? "—"}</dd></div>
              <div><dt>Mark</dt><dd>{product.markPrice ?? "—"}</dd></div>
              <div><dt>Ask</dt><dd>{product.bestAsk ?? "—"}</dd></div>
            </dl>
          </article>
        ))}
        {visibleProducts.length === 0 ? (
          <div className="market-rail__empty">
            {search ? "No matching Delta contracts." : "Search and add contracts to this watchlist."}
          </div>
        ) : null}
      </div>
    </aside>
  );
}

// Retained temporarily for backwards-compatible code splitting only. The
// /market route now redirects to the dashboard and no candle request is made.
function _MarketPage({ csrf }: { readonly csrf: string }) {
  const [products, setProducts] = useState<Product[]>([]);
  const [watchlist, setWatchlist] = useState<WatchlistRow[]>([]);
  const [candleProduct, setCandleProduct] = useState("");
  const [candles, setCandles] = useState<CandleRow[]>([]);
  const [fresh, setFresh] = useState<boolean | null>(null);
  const load = useCallback(() => {
    void Promise.all([
      request<{ snapshot: { fresh: boolean } | null; products: Product[] }>(
        "/api/v1/market/products",
      ),
      request<{ items: WatchlistRow[] }>("/api/v1/market/watchlist"),
    ])
      .then(([market, saved]) => {
        setProducts(market.products);
        setFresh(market.snapshot?.fresh ?? null);
        setWatchlist(saved.items);
      })
      .catch(() => {
        setProducts([]);
        setFresh(false);
      });
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") load();
    };
    const timer = window.setInterval(refresh, 5_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [load]);
  useEffect(() => {
    const selected = candleProduct || String(watchlist[0]?.productId ?? "");
    if (!selected) {
      setCandles([]);
      return;
    }
    setCandleProduct(selected);
    void request<{ items: CandleRow[] }>(
      `/api/v1/market/candles/${selected}?resolution=1m&limit=60`,
    )
      .then((value) => setCandles(value.items))
      .catch(() => setCandles([]));
  }, [candleProduct, watchlist]);
  async function toggle(product: Product) {
    const saved = watchlist.find(
      (item) => item.productId === product.productId,
    );
    if (saved)
      await request(
        `/api/v1/market/watchlist/${saved.id}`,
        { method: "DELETE" },
        csrf,
      );
    else
      await request(
        "/api/v1/market/watchlist",
        {
          method: "PUT",
          body: JSON.stringify({
            symbol: product.symbol,
            product_id: product.productId,
          }),
        },
        csrf,
      );
    load();
  }
  return (
    <>
      <Panel title="Delta India futures market">
        <div className="flex items-center justify-between p-4 text-sm">
          <span>
            Feed:{" "}
            <strong>
              {fresh === true
                ? "Fresh"
                : fresh === false
                  ? "Unavailable or stale"
                  : "Not published"}
            </strong>
          </span>
          <span>
            Watchlist: <strong>{watchlist.length}</strong>
          </span>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Watch</th>
                <th>Symbol</th>
                <th>Contract</th>
                <th>Mark</th>
                <th>Bid</th>
                <th>Ask</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => {
                const saved = watchlist.some(
                  (item) => item.productId === p.productId,
                );
                return (
                  <tr key={p.productId}>
                    <td>
                      <button
                        aria-label={`${saved ? "Remove" : "Add"} ${p.symbol} ${saved ? "from" : "to"} watchlist`}
                        className={saved ? "text-amber-600" : "text-slate-400"}
                        onClick={() => void toggle(p)}
                        type="button"
                      >
                        {saved ? "★" : "☆"}
                      </button>
                    </td>
                    <td>
                      <strong>{p.symbol}</strong>
                    </td>
                    <td>{p.contractType}</td>
                    <td>{p.markPrice ?? "—"}</td>
                    <td>{p.bestBid ?? "—"}</td>
                    <td>{p.bestAsk ?? "—"}</td>
                    <td>
                      <span className={p.quoteFresh ? "text-green-700" : "text-red-700"}>
                        {p.quoteFresh ? "Fresh" : "Stale"}
                      </span>{" "}
                      · {p.observedAt ?? "No quote"}
                    </td>
                  </tr>
                );
              })}
              {products.length === 0 ? (
                <tr>
                  <td colSpan={7}>
                    No catalogue has been published by the market worker.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Recent provisional 1-minute candles">
        <div className="p-4">
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setCandleProduct(event.target.value)}
            value={candleProduct}
          >
            <option value="">Select watchlist contract</option>
            {watchlist.map((item) => (
              <option key={item.id} value={item.productId}>
                {item.symbol}
              </option>
            ))}
          </select>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Open</th>
                <th>High</th>
                <th>Low</th>
                <th>Close</th>
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {candles.map((row) => (
                <tr key={row.timestamp}>
                  <td>{row.timestamp}</td>
                  <td>{row.open}</td>
                  <td>{row.high}</td>
                  <td>{row.low}</td>
                  <td>{row.close}</td>
                  <td>{row.volume ?? "—"}</td>
                </tr>
              ))}
              {candles.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    No streamed candle history is available yet.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function AccountsPage({
  user,
  csrf,
}: {
  readonly user: SessionUser;
  readonly csrf: string;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profileName, setProfileName] = useState("");
  const [credentialAccount, setCredentialAccount] = useState<string | null>(
    null,
  );
  const [editingAccount, setEditingAccount] = useState<string | null>(null);
  const [editingName, setEditingName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const load = useCallback(() => {
    void request<{ accounts: Account[] }>("/api/v1/accounts").then((v) =>
      setAccounts(v.accounts),
    );
  }, []);
  useEffect(load, [load]);
  async function create(event: FormEvent) {
    event.preventDefault();
    setCreating(true);
    setNotice(null);
    try {
      const created = await request<{ id: string }>(
        "/api/v1/accounts",
        {
          method: "POST",
          body: JSON.stringify({
            profile_name: profileName.trim(),
            environment: "prod",
          }),
        },
        csrf,
      );
      setProfileName("");
      setCredentialAccount(created.id);
      setNotice(
        "Live account profile created. Configure its Delta credentials next.",
      );
      load();
    } catch (error) {
      if (error instanceof RequestError && error.status === 401) {
        setNotice(
          "For security, sign out and sign in again before adding a Live account.",
        );
      } else if (
        error instanceof RequestError &&
        error.code === "account_name_exists"
      ) {
        setNotice("An account with this profile name already exists.");
      } else if (
        error instanceof RequestError &&
        error.code === "account_create_failed"
      ) {
        setNotice(
          "The account could not be saved. Support can trace this attempt using the server request ID.",
        );
      } else if (
        error instanceof RequestError &&
        error.code === "invalid_profile_name"
      ) {
        setNotice(
          "Use letters, numbers, spaces, hyphens, dots, colons, or underscores for the profile name.",
        );
      } else {
        setNotice("Account creation failed. No account was added.");
      }
    } finally {
      setCreating(false);
    }
  }
  async function saveCredentials(event: FormEvent) {
    event.preventDefault();
    if (credentialAccount === null) return;
    setNotice(null);
    try {
      await request(
        `/api/v1/accounts/${credentialAccount}/credentials`,
        {
          method: "POST",
          body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret }),
        },
        csrf,
      );
      setNotice(
        "Credentials stored in AWS Secrets Manager. Verify them from this EC2 static IP.",
      );
      setApiKey("");
      setApiSecret("");
      load();
    } catch (error) {
      if (error instanceof RequestError && error.status === 401)
        setNotice(
          "For security, sign out and sign in again before storing credentials.",
        );
      else if (error instanceof RequestError && error.status === 403)
        setNotice(
          "Your account does not have permission to manage these credentials.",
        );
      else
        setNotice(
          "Credential storage failed. Confirm the AWS secret policy and retry; no credential was saved in the application database.",
        );
    }
  }
  async function verify(accountId: string) {
    setNotice(null);
    try {
      await request(
        `/api/v1/accounts/${accountId}/verify`,
        { method: "POST" },
        csrf,
      );
      setNotice(
        "Delta credentials and static-IP access verified. Entries remain disabled pending reconciliation.",
      );
    } catch (error) {
      if (
        error instanceof RequestError &&
        error.code === "fresh_authentication_required"
      )
        setNotice("Sign out and sign in again, then verify within 15 minutes.");
      else if (
        error instanceof RequestError &&
        error.code === "credential_or_ip_rejected"
      )
        setNotice(
          "Delta rejected the key, secret, permissions, environment, or whitelisted EC2 IP.",
        );
      else if (
        error instanceof RequestError &&
        error.code === "verification_unavailable"
      )
        setNotice(
          "Delta verification is temporarily unavailable. The credential remains locked and entries stay disabled.",
        );
      else
        setNotice("Credential verification failed. No Live gate was enabled.");
    }
    load();
  }
  async function renameAccount(event: FormEvent) {
    event.preventDefault();
    if (editingAccount === null) return;
    setNotice(null);
    setRenaming(true);
    try {
      await request(
        `/api/v1/accounts/${editingAccount}`,
        {
          method: "PATCH",
          body: JSON.stringify({ profile_name: editingName.trim() }),
        },
        csrf,
      );
      setEditingAccount(null);
      setEditingName("");
      setNotice(
        "Account profile renamed. Trading history and assignments were preserved.",
      );
      load();
    } catch (error) {
      if (
        error instanceof RequestError &&
        error.code === "account_name_exists"
      )
        setNotice("An account with this profile name already exists.");
      else if (
        error instanceof RequestError &&
        error.code === "manage_permission_required"
      )
        setNotice("Manage access is required to rename this account.");
      else setNotice("The account profile could not be renamed.");
    } finally {
      setRenaming(false);
    }
  }
  async function archiveAccount(account: Account) {
    if (
      !window.confirm(
        `Archive ${account.profileName}? It will disappear from active account lists, but its audit and trading history will be preserved.`,
      )
    )
      return;
    setNotice(null);
    try {
      await request(
        `/api/v1/accounts/${account.id}`,
        { method: "DELETE" },
        csrf,
      );
      if (credentialAccount === account.id) setCredentialAccount(null);
      setNotice("Account archived. Historical evidence was preserved.");
      load();
    } catch (error) {
      if (
        error instanceof RequestError &&
        error.code === "fresh_authentication_required"
      )
        setNotice("Sign out and sign in again before archiving an account.");
      else if (
        error instanceof RequestError &&
        error.code === "account_gates_must_be_disabled"
      )
        setNotice("Disable both account Live gates before archiving.");
      else if (
        error instanceof RequestError &&
        error.code === "account_worker_active"
      )
        setNotice(
          "The private worker still owns an active lease. Wait for it to expire before archiving.",
        );
      else setNotice("The account could not be archived safely.");
    }
  }
  return (
    <div className="hmt-page">
      <div className="hmt-page-heading">
        <div>
          <h1>Delta Accounts</h1>
          <p>Connect and manage Delta Exchange India accounts</p>
        </div>
        {user.role === "admin" ? (
          <form
            className="hmt-account-create"
            onSubmit={(event) => void create(event)}
          >
            <input
              className="hmt-input"
              disabled={creating}
              onChange={(e) => setProfileName(e.target.value)}
              placeholder="Account profile name"
              required
              value={profileName}
            />
            <button
              className="hmt-primary-button"
              disabled={creating}
              type="submit"
            >
              {creating ? "Adding…" : "Add Live account"}
            </button>
          </form>
        ) : null}
      </div>
      <section className="data-panel hmt-account-panel">
        {notice ? (
          <p className="hmt-alert hmt-alert--info" role="status">
            {notice}
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Profile</th>
                <th>Status</th>
                <th>Credential</th>
                <th>Reconciliation</th>
                <th>Live gates</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td>
                    <strong>{a.profileName}</strong>
                    <small>Delta India · {a.environment} · {a.permission} · {a.activeProtectionCount ?? 0} protected</small>
                  </td>
                  <td>
                    <StatusBadge value={a.identityVerified ? "Connected" : "Setup required"} good={a.identityVerified} />
                    <small>{a.leaseActive ? "Worker lease active" : "Worker lease unavailable"}</small>
                  </td>
                  <td><StatusBadge value={a.credentialState} good={a.credentialState === "verified"} /></td>
                  <td><StatusBadge value={a.reconciliationState} good={a.reconciliationState === "ready"} /><small>{a.fresh ? "Fresh account state" : "Data stale or unavailable"}</small></td>
                  <td>
                    Entries {a.entriesEnabled ? "ON" : "OFF"} · Exits{" "}
                    {a.exitsEnabled ? "ON" : "OFF"}
                  </td>
                  <td>
                    {user.role === "admin" || a.permission === "manage" ? (
                      <details className="hmt-action-menu">
                        <summary aria-label={`Actions for ${a.profileName}`}><MoreVertical size={18} /></summary>
                        <div>
                          <button onClick={() => { setEditingAccount(a.id); setEditingName(a.profileName); }} type="button"><Pencil size={15} /> Edit account name</button>
                          <button onClick={() => setCredentialAccount(a.id)} type="button"><KeyRound size={15} /> Configure credentials</button>
                          <button disabled={!a.credentialConfigured} onClick={() => void verify(a.id)} type="button"><ShieldCheck size={15} /> Verify connection</button>
                          {user.role === "admin" ? <button className="is-danger" onClick={() => void archiveAccount(a)} type="button"><Trash2 size={15} /> Archive account</button> : null}
                        </div>
                      </details>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
              {accounts.length === 0 ? (
                <tr>
                  <td className="empty-cell" colSpan={6}>No assigned Delta accounts.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {credentialAccount !== null ? (
          <form
            className="grid gap-3 border-t p-4 md:grid-cols-2"
            onSubmit={(event) => void saveCredentials(event)}
          >
            <div className="md:col-span-2">
              <strong>Replace Delta API credentials</strong>
              <p className="text-xs text-slate-500">
                Use a production India key with the EC2 Elastic IP whitelisted.
                Values are never returned.
              </p>
            </div>
            <input
              autoComplete="off"
              className="rounded border px-3 py-2"
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="API key"
              required
              value={apiKey}
            />
            <input
              autoComplete="new-password"
              className="rounded border px-3 py-2"
              onChange={(e) => setApiSecret(e.target.value)}
              placeholder="API secret"
              required
              type="password"
              value={apiSecret}
            />
            <div className="flex gap-2 md:col-span-2">
              <button
                className="rounded bg-blue-600 px-4 py-2 text-white"
                type="submit"
              >
                Store credentials
              </button>
              <button
                className="rounded border px-4 py-2"
                onClick={() => {
                  setCredentialAccount(null);
                  setApiKey("");
                  setApiSecret("");
                }}
                type="button"
              >
                Cancel
              </button>
            </div>
          </form>
        ) : null}
      </section>
      {editingAccount !== null
        ? createPortal(
            <div
              className="hmt-modal-backdrop"
              onClick={(event) => {
                if (event.currentTarget === event.target) setEditingAccount(null);
              }}
              role="presentation"
            >
          <form
            aria-labelledby="edit-account-title"
            aria-modal="true"
            className="hmt-modal"
            onSubmit={(event) => void renameAccount(event)}
            role="dialog"
          >
            <div className="hmt-modal__heading">
              <div><h2 id="edit-account-title">Edit account</h2><p>Renaming only changes display metadata. Credentials and history remain linked.</p></div>
              <button aria-label="Close" onClick={() => setEditingAccount(null)} type="button"><X size={20} /></button>
            </div>
            <label>Account name<input autoFocus className="hmt-input" maxLength={128} minLength={1} onChange={(event) => setEditingName(event.target.value)} required value={editingName} /></label>
            <div className="hmt-modal__actions">
              <button className="hmt-secondary-button" onClick={() => setEditingAccount(null)} type="button">Cancel</button>
              <button className="hmt-primary-button" disabled={renaming || editingName.trim().length === 0} type="submit">{renaming ? "Saving…" : "Save changes"}</button>
            </div>
          </form>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

function OrderTicketDialog({
  user,
  csrf,
  readOnlyMode,
  onClose,
  onSubmitted,
}: {
  readonly user: SessionUser;
  readonly csrf: string;
  readonly readOnlyMode: boolean;
  readonly onClose: () => void;
  readonly onSubmitted: () => void;
}) {
  const canSelectAccount = (account: Account) =>
    account.permission !== "view" &&
    account.environment === "prod" &&
    account.identityVerified &&
    account.credentialState === "verified" &&
    account.reconciliationState === "ready" &&
    account.leaseActive === true;
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [riskPolicies, setRiskPolicies] = useState<Record<string, RiskPolicyView | null>>({});
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [productId, setProductId] = useState("");
  const [productSearch, setProductSearch] = useState("");
  const [productPickerOpen, setProductPickerOpen] = useState(false);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [contracts, setContracts] = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [reduceOnly, setReduceOnly] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    async function loadTicketData() {
      setLoading(true);
      setLoadError(null);
      try {
        const [a, p] = await Promise.all([
          request<{ accounts: Account[] }>("/api/v1/accounts"),
          request<{ products: Product[] }>("/api/v1/market/products"),
        ]);
        if (!active) return;
        setAccounts(a.accounts);
        const available = a.accounts.filter(canSelectAccount);
        setAccountIds(available[0] ? [available[0].id] : []);
        setProducts(p.products);
        const policies = await Promise.all(available.map(async (account) => {
          try {
            const value = await request<{
              configured: boolean;
              policy: RiskPolicyView | null;
              qualified: boolean;
              qualificationReason: string;
              qualificationWindowHours: number;
            }>(`/api/v1/risk/${account.id}`);
            return [account.id, value.configured && value.policy
              ? { ...value.policy, qualified: value.qualified, qualificationReason: value.qualificationReason, qualificationWindowHours: value.qualificationWindowHours }
              : null] as const;
          } catch {
            return [account.id, null] as const;
          }
        }));
        if (active) setRiskPolicies(Object.fromEntries(policies));
      } catch (error) {
        if (active) setLoadError(error instanceof RequestError ? `Could not load order data (${error.code}).` : "Could not load order data. Retry or refresh the page.");
      } finally {
        if (active) setLoading(false);
      }
    }
    void loadTicketData();
    return () => { active = false; };
  }, []);
  async function placeOrder(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    setSubmitting(true);
    try {
      const outcomes: string[] = [];
      for (const selectedAccountId of accountIds) {
        const account = accounts.find((item) => item.id === selectedAccountId);
        try {
          const preview = await request<OrderPreview>("/api/v1/trading/previews", {
            method: "POST",
            body: JSON.stringify({
              account_id: selectedAccountId,
              product_id: Number(productId),
              side,
              contracts: Number(contracts),
              order_type: orderType,
              limit_price: orderType === "limit" ? limitPrice : null,
              reduce_only: reduceOnly,
            }),
          }, csrf);
          const intent = await request<{ intentId: string; status: string }>("/api/v1/trading/intents", {
            method: "POST",
            body: JSON.stringify({ preview_id: preview.previewId }),
          }, csrf);
          outcomes.push(`${account?.profileName ?? selectedAccountId}: order queued; awaiting Delta acknowledgement (reference ${intent.intentId})`);
        } catch (error) {
          const reason = error instanceof RequestError && error.code === "insufficient_available_balance"
            ? "not placed: insufficient available balance for this contract. Reduce quantity or add funds; no order was sent."
            : error instanceof RequestError && error.code === "insufficient_margin"
              ? "not placed: available margin is too low for this order. Reduce quantity or add funds; no order was sent."
              : error instanceof RequestError && error.code === "insufficient_commission"
                ? "not placed: available funds do not cover estimated commission. Reduce quantity or add funds; no order was sent."
            : error instanceof RequestError && error.code === "qualification_scope_changed"
              ? "not placed: approved risk/product scope changed; requalify this scope in Administration → Health & Audit, then reopen account and global gates. No order was sent."
              : error instanceof RequestError && ["qualification_missing", "qualification_window_incomplete"].includes(error.code)
                ? "not placed: Live qualification is required. Complete qualification in Administration → Health & Audit, then reopen the gates. No order was sent."
                : error instanceof RequestError
                  ? `not placed (${error.code})`
                  : "not placed (request failed)";
          outcomes.push(`${account?.profileName ?? selectedAccountId}: ${reason}`);
        }
      }
      setNotice(outcomes.join(" · "));
      if (outcomes.some((outcome) => outcome.includes("order queued"))) onSubmitted();
    } catch (error) {
      const balanceMessages: Record<string, string> = {
        insufficient_available_balance: "Order not placed: available wallet balance is too low for this contract. Reduce the quantity or add funds.",
        insufficient_margin: "Order not placed: available margin is too low for this order. Reduce the quantity or add funds.",
        insufficient_commission: "Order not placed: available funds do not cover estimated commission. Reduce the quantity or add funds.",
      };
      setNotice(
        error instanceof RequestError && balanceMessages[error.code]
          ? balanceMessages[error.code]
          : error instanceof RequestError
            ? `Order not placed (${error.code}). Check account readiness, quote freshness, position and risk limits.`
            : "Order not placed. Check account readiness, quote freshness, position and risk limits.",
      );
    } finally {
      setSubmitting(false);
    }
  }
  const selectedProduct = products.find(
    (product) => product.productId === Number(productId),
  );
  const tradableAccounts = accounts.filter(
    canSelectAccount,
  );
  const filteredProducts = products
    .filter((product) => product.tradingStatus.toLowerCase() === "operational")
    .filter((product) => product.symbol.toLowerCase().includes(productSearch.trim().toLowerCase()));
  const selectedPolicies = accountIds.map((id) => riskPolicies[id] ?? null);
  const executablePrice = Number(
    side === "buy"
      ? selectedProduct?.bestAsk
      : selectedProduct?.bestBid,
  );
  const estimatedNotional =
    executablePrice > 0 && selectedProduct
      ? executablePrice * Number(selectedProduct.contractValue) * Number(contracts)
      : 0;
  const estimatedMargin =
    estimatedNotional * Number(selectedProduct?.initialMargin ?? "0");
  const productApproved = selectedProduct !== undefined && selectedPolicies.length > 0 && selectedPolicies.every((policy) => Boolean(policy?.enabled && policy.allowedProductIds.includes(selectedProduct.productId)));
  const qualificationFailures = selectedPolicies.filter((policy) => !policy?.qualified);
  const qualificationReady = selectedPolicies.length > 0 && qualificationFailures.length === 0;
  const canTrade =
    !readOnlyMode && user.role !== "viewer" && accountIds.length > 0;
  return createPortal(
    <div className="hmt-modal-backdrop hmt-order-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section aria-labelledby="place-order-title" aria-modal="true" className="hmt-order-dialog" role="dialog">
        <header className="hmt-order-dialog__header"><div><h2 id="place-order-title">Place Order</h2><p>Delta Exchange India · one contract applied to each selected account</p></div><button aria-label="Close order ticket" className="hmt-icon-button" onClick={onClose} type="button"><X /></button></header>
        {loading ? <p className="hmt-alert" role="status">Loading accounts and live contracts…</p> : null}
        {loadError ? <p className="hmt-alert hmt-alert--error" role="alert">{loadError}</p> : null}
        {!loading && !loadError && tradableAccounts.length === 0 ? <p className="hmt-alert hmt-alert--warning" role="status">No accounts are ready to trade. Verify a production account and wait for fresh reconciliation.</p> : null}
        {readOnlyMode ? (
          <p className="hmt-alert hmt-alert--warning">
            Qualification mode — order previews and submissions remain disabled.
          </p>
        ) : null}
        <form
          className="hmt-order-ticket"
          onSubmit={(event) => void placeOrder(event)}
        >
          <div className="hmt-order-accounts"><div className="hmt-order-accounts__heading"><strong>Select Accounts ({accountIds.length} selected)</strong><button className="hmt-text-button" disabled={!tradableAccounts.length} onClick={() => setAccountIds(accountIds.length === tradableAccounts.length ? [] : tradableAccounts.map((account) => account.id))} type="button">{accountIds.length === tradableAccounts.length ? "Clear selection" : "Select all"}</button></div><div className="hmt-order-accounts__grid">{accounts.map((account) => { const checked = accountIds.includes(account.id); const disabled = !canSelectAccount(account); const reason = account.permission === "view" ? "View only" : account.environment !== "prod" ? "Testnet excluded" : account.credentialState !== "verified" ? "Verify credentials" : account.reconciliationState !== "ready" || account.leaseActive !== true ? "Account not ready" : "Delta India · Live"; return <label className={`hmt-order-account ${checked ? "is-selected" : ""} ${disabled ? "is-disabled" : ""}`} key={account.id}><input checked={checked} disabled={disabled} onChange={() => setAccountIds((selected) => checked ? selected.filter((id) => id !== account.id) : [...selected, account.id])} type="checkbox" /><span>{account.profileName}<small>{reason}</small></span></label>; })}</div></div>
          <div className="hmt-order-search"><label htmlFor="crypto-contract-search">Contract</label><div className="hmt-order-search__control"><Search size={18} /><input autoComplete="off" id="crypto-contract-search" onChange={(event) => { setProductSearch(event.target.value); setProductPickerOpen(true); setProductId(""); }} onFocus={() => setProductPickerOpen(true)} placeholder="Search crypto contracts, e.g. BTCUSD" value={productSearch || selectedProduct?.symbol || ""} /><button aria-label="Show contracts" onClick={() => setProductPickerOpen((open) => !open)} type="button"><ChevronDown size={16} /></button>{productPickerOpen ? <div className="hmt-order-search__results" role="listbox">{filteredProducts.map((product) => <button aria-selected={Number(productId) === product.productId} key={product.productId} onClick={() => { setProductId(String(product.productId)); setProductSearch(""); setProductPickerOpen(false); }} role="option" type="button"><span>{product.symbol}</span><small>{product.contractType.replaceAll("_", " ")} · {product.quoteFresh ? "Live quote" : "Quote stale"}</small></button>)}{filteredProducts.length === 0 ? <p>No active contracts match that search.</p> : null}</div> : null}</div></div>
          <div className="hmt-order-segment" role="group" aria-label="Position side">
            <button className={side === "buy" ? "is-long" : ""} onClick={() => setSide("buy")} type="button">Long / Buy</button>
            <button className={side === "sell" ? "is-short" : ""} onClick={() => setSide("sell")} type="button">Short / Sell</button>
          </div>
          <div className="hmt-order-segment" role="group" aria-label="Order type">
            <button className={orderType === "market" ? "is-active" : ""} onClick={() => setOrderType("market")} type="button">Market</button>
            <button className={orderType === "limit" ? "is-active" : ""} onClick={() => setOrderType("limit")} type="button">Limit</button>
          </div>
          <div className="hmt-order-ticket__grid">
            <label>Contracts<input className="hmt-input" min="1" step="1" onChange={(event) => setContracts(event.target.value)} required type="number" value={contracts} /><small>Whole contracts only. {selectedProduct ? `1 contract = ${selectedProduct.contractValue} ${selectedProduct.contractUnitCurrency}` : "Select a contract"}</small></label>
          {orderType === "limit" ? (
            <label>Limit price (USD)<input
              className="hmt-input"
              onChange={(event) => setLimitPrice(event.target.value)}
              placeholder={selectedProduct?.bestBid ?? "Limit price"}
              required
              value={limitPrice}
            /></label>
          ) : (
            <div className="hmt-order-quote"><small>Executable quote</small><strong>{side === "buy" ? selectedProduct?.bestAsk ?? "—" : selectedProduct?.bestBid ?? "—"} USD</strong><span>{selectedProduct?.quoteFresh ? "Fresh" : "Stale"}</span></div>
          )}
          </div>
          <div className="hmt-order-metrics">
            <div><small>Leverage</small><strong>{selectedProduct?.defaultLeverage ? `${selectedProduct.defaultLeverage}× maximum` : "Exchange managed"}</strong><small>Exchange controlled</small></div>
            <div><small>Est. notional</small><strong>${estimatedNotional ? estimatedNotional.toFixed(4) : "—"}</strong></div>
            <div><small>Est. initial margin</small><strong>${estimatedMargin ? estimatedMargin.toFixed(4) : "—"}</strong></div>
            <div><small>Required protection</small><strong>SL 2% · TP 4% · Trail 1%</strong></div>
          </div>
          <p className={productApproved && qualificationReady ? "hmt-order-scope is-approved" : "hmt-order-scope"}>{!productApproved ? "Select a contract approved by every selected account. Admins can configure eligible contracts in Risk Management." : !qualificationReady ? `This risk/product scope is not qualified (${qualificationFailures[0]?.qualificationReason ?? "qualification_missing"}). Requalify in Administration → Health & Audit before placing an order; changing the allowlist closes the gates. No order will be sent.` : "Contract is approved by every selected account and the current scope is qualified."}</p>
          <label className="flex items-center gap-2">
            <input
              checked={reduceOnly}
              onChange={(event) => setReduceOnly(event.target.checked)}
              type="checkbox"
            />
            Reduce-only exit
          </label>
          {notice ? (
            <p className="hmt-order-notice" role="status">
              {notice}
            </p>
          ) : null}
          <footer className="hmt-order-ticket__footer">
            <span>{accountIds.length} account(s) selected</span>
            <div><button className="hmt-secondary-button" onClick={onClose} type="button">Cancel</button><button className={`hmt-order-submit ${side === "sell" ? "is-short" : ""}`} disabled={loading || Boolean(loadError) || submitting || !canTrade || !productId || !productApproved || !qualificationReady || !selectedProduct?.quoteFresh || (orderType === "limit" && !limitPrice)} type="submit">{submitting ? "Submitting…" : `${side === "buy" ? "Buy / Long" : "Sell / Short"} immediately`}</button></div>
          </footer>
        </form>
      </section>
    </div>, document.body,
  );
}

function AccountStatePage({
  mode,
  user,
  csrf,
  readOnlyMode,
}: {
  readonly mode: "orders" | "positions";
  readonly user: SessionUser;
  readonly csrf: string;
  readonly readOnlyMode: boolean;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [state, setState] = useState<AccountState | null>(null);
  const [orderIntents, setOrderIntents] = useState<LocalOrderIntent[]>([]);
  const [actions, setActions] = useState<LiveOrderActionRow[]>([]);
  const [modifyOrder, setModifyOrder] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [modifySize, setModifySize] = useState("");
  const [modifyPrice, setModifyPrice] = useState("");
  const [exitingPosition, setExitingPosition] = useState(false);
  const [cancellingIntentId, setCancellingIntentId] = useState<string | null>(null);
  const [showOrderTicket, setShowOrderTicket] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(
    (selected: string) => {
      if (!selected) return;
      setError(null);
      void request<AccountState>(`/api/v1/accounts/${selected}/state`)
        .then(setState)
        .catch(() => {
          setState(null);
          setError("Reconciled account state is unavailable or stale.");
        });
      if (mode === "orders") {
        void request<{ actions: LiveOrderActionRow[] }>(
          `/api/v1/trading/actions?account_id=${encodeURIComponent(selected)}`,
        ).then((value) => setActions(value.actions)).catch(() => setActions([]));
        void request<{ intents: LocalOrderIntent[] }>(
          `/api/v1/accounts/${encodeURIComponent(selected)}/order-intents`,
        ).then((value) => setOrderIntents(value.intents)).catch(() => setOrderIntents([]));
      }
    },
    [mode],
  );
  useEffect(() => {
    void request<{ accounts: Account[] }>("/api/v1/accounts").then((value) => {
      setAccounts(value.accounts);
      const first = value.accounts[0]?.id ?? "";
      setAccountId(first);
      load(first);
    });
  }, [load]);
  useEffect(() => {
    if (!accountId) return;
    const refresh = () => {
      if (document.visibilityState === "visible") load(accountId);
    };
    const timer = window.setInterval(refresh, 15_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [accountId, load]);
  const selected = accounts.find((account) => account.id === accountId);
  const canTrade =
    !readOnlyMode && user.role !== "viewer" && selected?.permission !== "view";
  async function queueAction(
    row: Record<string, unknown>,
    action: "cancel" | "modify",
  ) {
    const orderId = Number(row.id ?? row.order_id);
    const productId = Number(row.product_id);
    if (!Number.isSafeInteger(orderId) || !Number.isSafeInteger(productId)) {
      setError("Order identity is not valid for mutation.");
      return;
    }
    try {
      await request(
        "/api/v1/trading/actions",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            action,
            order_id: orderId,
            product_id: productId,
            size: action === "modify" ? Number(modifySize) : null,
            limit_price:
              action === "modify" && modifyPrice ? modifyPrice : null,
          }),
        },
        csrf,
      );
      setError(
        action === "cancel"
          ? "Cancellation queued; waiting for Delta acknowledgement."
          : "Modification queued; waiting for Delta acknowledgement.",
      );
      setModifyOrder(null);
      setModifySize("");
      setModifyPrice("");
      void load(accountId);
    } catch {
      setError(
        "Action rejected. Refresh account state and verify the Live gates.",
      );
    }
  }
  async function cancelQueuedIntent(intentId: string) {
    setCancellingIntentId(intentId);
    setError(null);
    try {
      await request(
        `/api/v1/accounts/${encodeURIComponent(accountId)}/order-intents/${encodeURIComponent(intentId)}/cancel`,
        { method: "POST", body: JSON.stringify({}) },
        csrf,
      );
      setError("Order cancelled before dispatch. It was not sent to Delta.");
      void load(accountId);
    } catch (requestError) {
      const message = requestError instanceof RequestError
        ? requestError.code === "submission_already_claimed"
          ? "The worker already claimed this order. Check Open Delta orders and Delta before taking another action."
          : requestError.code === "worker_lease_unavailable"
            ? "The worker lease is unavailable. Refresh order state before retrying."
            : `Could not cancel queued order (${requestError.code}). Refresh and check its dispatch status.`
        : "Could not cancel queued order. Refresh and check its dispatch status.";
      setError(message);
      void load(accountId);
    } finally {
      setCancellingIntentId(null);
    }
  }
  async function exitPositionImmediately(row: Record<string, unknown>) {
    const productId = Number(row.product_id ?? row.productId);
    const signedSize = Number(row.size ?? row.contracts);
    const contracts = Math.abs(signedSize);
    const rowSide = String(row.side ?? "").toLowerCase();
    const side: "buy" | "sell" =
      signedSize < 0 || rowSide === "short" || rowSide === "sell" ? "buy" : "sell";
    if (!Number.isSafeInteger(productId) || !Number.isSafeInteger(contracts) || contracts < 1) {
      setError("Position size is not a valid whole-contract exit quantity.");
      return;
    }
    setExitingPosition(true);
    try {
      const preview = await request<OrderPreview>(
        "/api/v1/trading/previews",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            product_id: productId,
            side,
            contracts,
            order_type: "market",
            limit_price: null,
            reduce_only: true,
          }),
        },
        csrf,
      );
      const intent = await request<{ intentId: string }>(
        "/api/v1/trading/intents",
        {
          method: "POST",
          body: JSON.stringify({ preview_id: preview.previewId }),
        },
        csrf,
      );
      setError(`Reduce-only exit queued; awaiting Delta acknowledgement (reference ${intent.intentId}).`);
      load(accountId);
    } catch (requestError) {
      setError(
        requestError instanceof RequestError
          ? `Exit not placed (${requestError.code}). Refresh the position and retry.`
          : "Exit not placed. Refresh the position and retry.",
      );
    } finally {
      setExitingPosition(false);
    }
  }
  return (
    <>
      <Panel
        title={
          mode === "orders"
            ? "Orders"
            : "Positions"
        }
      >
        <div className={`hmt-orders-toolbar ${mode === "orders" ? "is-orders" : ""}`}>
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => {
              setAccountId(event.target.value);
              load(event.target.value);
            }}
            value={accountId}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.profileName}
              </option>
            ))}
          </select>
          <button
            className="hmt-secondary-button"
            onClick={() => load(accountId)}
            type="button"
          >
            <RefreshCw size={16} /> Refresh
          </button>
          <span className="self-center text-sm">
            {state
              ? `${state.fresh ? "Fresh" : "Stale"} · ${state.ageSeconds}s · reconciliation ${state.reconciliationState} · lease ${state.leaseActive ? "active" : "inactive"} · credential v${state.credentialVersion}`
              : "No state"}
          </span>
          {mode === "orders" ? (
            <button
              className="hmt-primary-button hmt-orders-toolbar__place"
              disabled={readOnlyMode || user.role === "viewer"}
              onClick={() => setShowOrderTicket(true)}
              type="button"
            >
              <Plus size={16} /> Place Order
            </button>
          ) : null}
        </div>
        {error ? (
          <p className="px-4 pb-4" role="status">
            {error}
          </p>
        ) : null}
        {mode === "orders" ? (
          <>
            <section className="hmt-data-section">
              <header>
                <div>
                  <h3>Order dispatch</h3>
                  <p>Cancel an unclaimed order here. Once the worker claims it, use the acknowledged Delta order below to cancel or modify it.</p>
                </div>
                <a className="hmt-secondary-button" href="#open-delta-orders">View open Delta orders</a>
              </header>
              <div className="overflow-x-auto"><table className="hmt-trading-table">
                <thead><tr><th>Submitted</th><th>Contract</th><th>Side</th><th>Type / size</th><th>Reference price</th><th>Dispatch</th><th>Delta order</th><th>Details</th><th>Actions</th></tr></thead>
                <tbody>
                  {orderIntents.map((intent) => {
                    const acknowledgedOrder = intent.exchangeOrderId == null
                      ? undefined
                      : state?.openOrders.find((order) => Number(order.id ?? order.order_id) === intent.exchangeOrderId);
                    return <tr key={intent.intentId}>
                    <td>{new Date(intent.createdAt).toLocaleString()}</td>
                    <td><strong>{intent.symbol}</strong><small>Product {intent.productId}</small></td>
                    <td><span className={`hmt-side-badge ${intent.side.toLowerCase()}`}>{intent.side.toUpperCase()}</span></td>
                    <td>{intent.orderType.toUpperCase()} · {intent.contracts}</td>
                    <td>{intent.price}</td>
                    <td><span className={`hmt-status-badge ${intent.submissionStatus.toLowerCase()}`}>{intent.submissionStatus.replaceAll("_", " ")}</span></td>
                    <td>{intent.exchangeOrderId ?? "Awaiting acknowledgement"}</td>
                    <td>{intent.errorCode ?? (intent.exchangeOrderId ? "Confirmed by Delta" : intent.submissionStatus === "PENDING" ? "Waiting for worker claim" : intent.submissionStatus === "UNCERTAIN" ? "Check Delta before retrying" : "Waiting for Delta acknowledgement")}</td>
                    <td>{acknowledgedOrder && canTrade ? <div className="hmt-row-actions"><button onClick={() => void queueAction(acknowledgedOrder, "cancel")} type="button">Cancel</button><button onClick={() => { setModifyOrder(acknowledgedOrder); setModifySize(String(acknowledgedOrder.size ?? "")); setModifyPrice(String(acknowledgedOrder.limit_price ?? "")); }} type="button">Modify</button></div> : intent.submissionStatus === "PENDING" && canTrade ? <div className="hmt-row-actions"><button disabled={cancellingIntentId === intent.intentId} onClick={() => void cancelQueuedIntent(intent.intentId)} type="button">{cancellingIntentId === intent.intentId ? "Cancelling…" : "Cancel"}</button><button disabled title="A queued order cannot be edited. Cancel it and place a replacement, or wait until Delta acknowledges it." type="button">Modify</button></div> : <span className="hmt-inline-status">{intent.submissionStatus === "PENDING" ? "Trading permission required" : intent.exchangeOrderId ? "Refreshing order state" : intent.submissionStatus === "CLAIMED" || intent.submissionStatus === "PREPARED" || intent.submissionStatus === "SENDING" || intent.submissionStatus === "UNCERTAIN" ? "Worker claimed · check Delta status" : "No action available"}</span>}</td>
                  </tr>)}
                  {orderIntents.length === 0 ? <tr><td colSpan={9}>No locally submitted orders.</td></tr> : null}
                </tbody>
              </table></div>
            </section>
            <section className="hmt-data-section" id="open-delta-orders">
              <header><div><h3>Open Delta orders</h3><p>Reconciled from the selected account · {state?.fresh ? "Fresh" : "Stale or unavailable"}</p></div></header>
              <div className="overflow-x-auto"><table className="hmt-trading-table">
                <thead><tr><th>Contract</th><th>Side</th><th>Type</th><th>Unfilled / size</th><th>Limit price</th><th>Status</th><th>Actions</th></tr></thead>
                <tbody>
                  {(state?.openOrders ?? []).map((row, index) => <tr key={String(row.id ?? index)}>
                    <td><strong>{String(row.product_symbol ?? `Product ${row.product_id ?? "—"}`)}</strong><small>Order {String(row.id ?? "—")}</small></td>
                    <td><span className={`hmt-side-badge ${String(row.side ?? "").toLowerCase()}`}>{String(row.side ?? "—").toUpperCase()}</span></td>
                    <td>{String(row.order_type ?? "—").replaceAll("_", " ").toUpperCase()}</td>
                    <td>{String(row.unfilled_size ?? "—")} / {String(row.size ?? "—")}</td>
                    <td>{String(row.limit_price ?? "Market")}</td>
                    <td><span className="hmt-status-badge open">{String(row.state ?? "Open")}</span></td>
                    <td>{canTrade ? <div className="hmt-row-actions"><button onClick={() => void queueAction(row, "cancel")} type="button">Cancel</button><button onClick={() => { setModifyOrder(row); setModifySize(String(row.size ?? "")); setModifyPrice(String(row.limit_price ?? "")); }} type="button">Modify</button></div> : "—"}</td>
                  </tr>)}
                  {(state?.openOrders ?? []).length === 0 ? <tr><td colSpan={7}>No open Delta orders.</td></tr> : null}
                </tbody>
              </table></div>
            </section>
          </>
        ) : (
          <section className="hmt-data-section">
            <header><div><h3>Open positions</h3><p>Reconciled perpetual positions · {state?.fresh ? "Fresh" : "Stale or unavailable"}</p></div></header>
            <div className="overflow-x-auto"><table className="hmt-trading-table">
              <thead><tr><th>Contract</th><th>Side</th><th>Contracts</th><th>Entry</th><th>Mark</th><th>Liquidation</th><th>Margin</th><th>Unrealized P&amp;L</th><th>Action</th></tr></thead>
              <tbody>
                {(state?.positions ?? []).map((row, index) => {
                  const size = Number(row.size ?? row.contracts ?? 0);
                  const pnl = Number(row.unrealized_pnl ?? 0);
                  return <tr key={`${String(row.product_id ?? index)}-${index}`}>
                    <td><strong>{String(row.product_symbol ?? `Product ${row.product_id ?? "—"}`)}</strong><small>{String(row.margin_mode ?? "Perpetual")}</small></td>
                    <td><span className={`hmt-side-badge ${size < 0 ? "sell" : "buy"}`}>{size < 0 ? "SHORT" : "LONG"}</span></td>
                    <td>{Math.abs(size)}</td><td>{String(row.entry_price ?? "—")}</td><td>{String(row.mark_price ?? "—")}</td><td>{String(row.liquidation_price ?? "—")}</td><td>{String(row.margin ?? "—")}</td>
                    <td className={pnl < 0 ? "hmt-negative" : "hmt-positive"}>{String(row.unrealized_pnl ?? "—")}</td>
                    <td>{canTrade ? <button className="hmt-exit-button" disabled={exitingPosition} onClick={() => void exitPositionImmediately(row)} type="button">{exitingPosition ? "Sending…" : "Exit now"}</button> : "—"}</td>
                  </tr>;
                })}
                {(state?.positions ?? []).length === 0 ? <tr><td colSpan={9}>No open positions.</td></tr> : null}
              </tbody>
            </table></div>
          </section>
        )}
        {!readOnlyMode && modifyOrder ? (
          <form
            className="grid gap-3 border-t p-4 md:grid-cols-3"
            onSubmit={(event) => {
              event.preventDefault();
              void queueAction(modifyOrder, "modify");
            }}
          >
            <div className="md:col-span-3">
              <strong>
                Modify order {String(modifyOrder.id ?? modifyOrder.order_id)}
              </strong>
              <p className="text-xs text-slate-500">
                For safety, size may only stay the same or decrease and cannot
                be less than the already-filled size.
              </p>
            </div>
            <input
              className="rounded border px-3 py-2"
              min="1"
              onChange={(event) => setModifySize(event.target.value)}
              required
              type="number"
              value={modifySize}
            />
            <input
              className="rounded border px-3 py-2"
              onChange={(event) => setModifyPrice(event.target.value)}
              placeholder="Limit price (optional)"
              value={modifyPrice}
            />
            <div className="flex gap-2">
              <button
                className="rounded bg-blue-600 px-3 py-2 text-white"
                type="submit"
              >
                Queue modification
              </button>
              <button
                className="rounded border px-3 py-2"
                onClick={() => setModifyOrder(null)}
                type="button"
              >
                Close
              </button>
            </div>
          </form>
        ) : null}
      </Panel>
      {mode === "positions" ? <Panel title="Balances and wallet activity">
        <section className="hmt-data-section"><header><div><h3>Balances</h3><p>Available funds and margin held at Delta</p></div></header>
          <div className="overflow-x-auto"><table className="hmt-trading-table"><thead><tr><th>Asset</th><th>Available</th><th>Wallet balance</th><th>Blocked margin</th></tr></thead><tbody>
            {(state?.balances ?? []).map((row, i) => <tr key={`${String(row.asset_id ?? i)}-${i}`}><td><strong>{String(row.asset_symbol ?? `Asset ${row.asset_id ?? "—"}`)}</strong></td><td>{String(row.available_balance ?? "—")}</td><td>{String(row.balance ?? "—")}</td><td>{String(row.blocked_margin ?? "—")}</td></tr>)}
            {(state?.balances ?? []).length === 0 ? <tr><td colSpan={4}>No reconciled balances.</td></tr> : null}
          </tbody></table></div>
        </section>
        <section className="hmt-data-section"><header><div><h3>Funding, fees and wallet activity</h3><p>Latest reconciled wallet transactions</p></div></header>
          <div className="overflow-x-auto"><table className="hmt-trading-table"><thead><tr><th>Time</th><th>Activity</th><th>Asset</th><th>Amount</th><th>Balance after</th><th>Contract</th></tr></thead><tbody>
            {(state?.walletTransactions ?? []).map((row, i) => <tr key={String(row.id ?? i)}><td>{String(row.created_at ?? "—")}</td><td>{String(row.transaction_type ?? "—").replaceAll("_", " ")}</td><td>{String(row.asset_symbol ?? `Asset ${row.asset_id ?? "—"}`)}</td><td>{String(row.amount ?? "—")}</td><td>{String(row.balance ?? "—")}</td><td>{String(row.product_id ?? "—")}</td></tr>)}
            {(state?.walletTransactions ?? []).length === 0 ? <tr><td colSpan={6}>No reconciled wallet activity.</td></tr> : null}
          </tbody></table></div>
        </section>
      </Panel> : null}
      {mode === "orders" ? <Panel title="Recent fills">
        <div className="overflow-x-auto"><table className="hmt-trading-table"><thead><tr><th>Time</th><th>Contract</th><th>Side</th><th>Contracts</th><th>Fill price</th><th>Commission</th><th>Role</th><th>Order ID</th></tr></thead><tbody>
          {(state?.fills ?? []).map((row, i) => <tr key={String(row.id ?? i)}><td>{String(row.created_at ?? "—")}</td><td><strong>{String(row.product_symbol ?? `Product ${row.product_id ?? "—"}`)}</strong></td><td><span className={`hmt-side-badge ${String(row.side ?? "").toLowerCase()}`}>{String(row.side ?? "—").toUpperCase()}</span></td><td>{String(row.size ?? "—")}</td><td>{String(row.price ?? "—")}</td><td>{String(row.commission ?? "—")}</td><td>{String(row.role ?? "—")}</td><td>{String(row.order_id ?? "—")}</td></tr>)}
          {(state?.fills ?? []).length === 0 ? <tr><td colSpan={8}>No recent reconciled fills.</td></tr> : null}
        </tbody></table></div>
      </Panel> : null}
      {mode === "orders" && showOrderTicket ? (
        <OrderTicketDialog
          csrf={csrf}
          onClose={() => setShowOrderTicket(false)}
          onSubmitted={() => load(accountId)}
          readOnlyMode={readOnlyMode}
          user={user}
        />
      ) : null}
      {mode === "orders" && !readOnlyMode ? (
        <Panel title="Cancel and modification lifecycle">
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Action</th>
                  <th>Order</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((action) => (
                  <tr key={action.id}>
                    <td>{action.createdAt}</td>
                    <td>{action.action}</td>
                    <td>
                      {action.orderId} · {action.productId}
                    </td>
                    <td>
                      {action.targetSize ?? "—"} ·{" "}
                      {action.targetLimitPrice ?? "—"}
                    </td>
                    <td>{action.status}</td>
                    <td>
                      {action.lastErrorCode ??
                        action.resolvedAt ??
                        "Awaiting worker"}
                    </td>
                  </tr>
                ))}
                {actions.length === 0 ? (
                  <tr>
                    <td colSpan={6}>No order actions.</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : null}
    </>
  );
}

function RecordTable({
  rows,
  empty,
}: {
  readonly rows: Record<string, unknown>[];
  readonly empty: string;
}) {
  const columns = Array.from(
    new Set(rows.flatMap((row) => Object.keys(row))),
  ).slice(0, 10);
  const text = (value: unknown) =>
    value === null || value === undefined
      ? "—"
      : typeof value === "object"
        ? JSON.stringify(value)
        : String(value);
  return (
    <div className="overflow-x-auto border-t">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.id ?? row.asset_symbol ?? index)}>
              {columns.map((column) => (
                <td key={column}>{text(row[column])}</td>
              ))}
            </tr>
          ))}
          {rows.length === 0 ? (
            <tr>
              <td colSpan={Math.max(1, columns.length)}>{empty}</td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}

function GttPage({
  user,
  csrf,
  readOnlyMode,
}: {
  readonly user: SessionUser;
  readonly csrf: string;
  readonly readOnlyMode: boolean;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [accountId, setAccountId] = useState("");
  const [productId, setProductId] = useState("");
  const [rows, setRows] = useState<GttRow[]>([]);
  const [side, setSide] = useState<"buy" | "sell">("sell");
  const [contracts, setContracts] = useState("1");
  const [kind, setKind] = useState<GttRow["triggerKind"]>("stop_loss");
  const [trigger, setTrigger] = useState("");
  const [trail, setTrail] = useState("");
  const [limit, setLimit] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("all");
  const [showCreate, setShowCreate] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const load = useCallback((selected: string) => {
    if (selected)
      void request<{ orders: GttRow[] }>(
        `/api/v1/gtt?account_id=${encodeURIComponent(selected)}`,
      ).then((value) => setRows(value.orders));
  }, []);
  useEffect(() => {
    void Promise.all([
      request<{ accounts: Account[] }>("/api/v1/accounts"),
      request<{ products: Product[] }>("/api/v1/market/products"),
    ]).then(([a, p]) => {
      setAccounts(a.accounts);
      setProducts(p.products);
      const first = a.accounts[0]?.id ?? "";
      setAccountId(first);
      setProductId(String(p.products[0]?.productId ?? ""));
      load(first);
    });
  }, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && accountId) load(accountId);
    }, 5_000);
    return () => window.clearInterval(timer);
  }, [accountId, load]);
  async function create(event: FormEvent) {
    event.preventDefault();
    const product = products.find(
      (item) => item.productId === Number(productId),
    );
    try {
      await request(
        "/api/v1/gtt",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            product_id: Number(productId),
            symbol: product?.symbol ?? String(productId),
            side,
            contracts: Number(contracts),
            trigger_kind: kind,
            trigger_price: kind === "trailing_stop" ? null : trigger,
            trail_offset: kind === "trailing_stop" ? trail : null,
            limit_price: limit || null,
          }),
        },
        csrf,
      );
      setNotice("Protected exit armed.");
      setShowCreate(false);
      load(accountId);
    } catch {
      setNotice(
        "GTT rejected. Confirm Live readiness, fresh quotes and risk policy.",
      );
    }
  }
  async function cancel(id: string) {
    await request(`/api/v1/gtt/${id}/cancel`, { method: "POST" }, csrf);
    load(accountId);
  }
  const canTrade =
    !readOnlyMode &&
    user.role !== "viewer" &&
    accounts.find((account) => account.id === accountId)?.permission !== "view";
  const accountName =
    accounts.find((account) => account.id === accountId)?.profileName ?? "—";
  const filteredRows = rows.filter(
    (row) =>
      (kindFilter === "all" || row.triggerKind === kindFilter) &&
      row.symbol.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const quoteFor = (row: GttRow) =>
    products.find((product) => product.productId === row.productId);
  const allSelected =
    filteredRows.length > 0 && filteredRows.every((row) => selected.has(row.id));
  return (
    <div className="hmt-page gtt-page">
      <div className="hmt-page-heading gtt-page__heading">
        <div className="gtt-page__title-row">
          <h1>HMT GTT ({rows.length})</h1>
          <button className="hmt-primary-button" disabled={!canTrade} onClick={() => setShowCreate(true)} type="button">
            <Plus size={18} /> New HMT GTT
          </button>
        </div>
        <div className="gtt-page__bulk-actions">
          <button disabled={selected.size === 0 || !canTrade} type="button"><Pencil size={16} /> Edit selected</button>
          <button disabled={selected.size === 0 || !canTrade} onClick={() => void Promise.all([...selected].map(cancel)).then(() => { setSelected(new Set()); load(accountId); })} type="button"><Trash2 size={16} /> Cancel selected</button>
          <span>Selected actions&nbsp; {selected.size}</span>
        </div>
      </div>

      <div className="gtt-page__engine-bar">
        <span><Activity size={16} /> GTT engine {readOnlyMode ? "monitoring" : "active"}</span>
        <span>Heartbeat: {new Date().toLocaleTimeString()}</span>
        <span className={products.some((product) => product.quoteFresh) ? "is-ready" : "is-blocked"}><Radio size={16} /> Prices {products.some((product) => product.quoteFresh) ? "connected" : "stale"}</span>
        {readOnlyMode ? <strong>Qualification mode · no exchange writes</strong> : null}
      </div>

      <div className="gtt-page__filters">
        <label className="gtt-search"><Search size={18} /><input aria-label="Search instrument" onChange={(event) => setSearch(event.target.value)} placeholder="Search instrument" value={search} /></label>
        <select
          aria-label="Account"
          onChange={(event) => {
            setAccountId(event.target.value);
            load(event.target.value);
          }}
          value={accountId}
        >
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.profileName}
            </option>
          ))}
        </select>
        <select aria-label="Trade type" onChange={(event) => setKindFilter(event.target.value)} value={kindFilter}>
          <option value="all">All protection types</option>
          <option value="stop_loss">Stop loss</option>
          <option value="target">Target</option>
          <option value="trailing_stop">Trailing stop</option>
        </select>
        <select aria-label="Instrument" onChange={(event) => setSearch(event.target.value)} value={products.some((product) => product.symbol === search) ? search : ""}>
          <option value="">All instruments</option>
          {products.map((product) => <option key={product.productId} value={product.symbol}>{product.symbol}</option>)}
        </select>
      </div>

      {notice ? <p className="gtt-page__notice" role="status">{notice}</p> : null}

      <div className="gtt-table-wrap">
        <table className="gtt-table">
          <thead><tr><th><input aria-label="Select all" checked={allSelected} onChange={(event) => setSelected(event.target.checked ? new Set(filteredRows.map((row) => row.id)) : new Set())} type="checkbox" /></th><th>Instrument</th><th>Account</th><th>Trigger / protection</th><th>Qty.</th><th>LTP / mark</th><th>Status</th><th aria-label="Actions" /></tr></thead>
          <tbody>
            {filteredRows.map((row) => {
              const quote = quoteFor(row);
              return <tr key={row.id}>
                <td><input aria-label={`Select ${row.symbol}`} checked={selected.has(row.id)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(row.id); else next.delete(row.id); return next; })} type="checkbox" /></td>
                <td><strong>{row.symbol}</strong><small>Delta India · perpetual</small><div className="gtt-tags"><span>OCO</span><span className={`is-${row.side}`}>{row.side.toUpperCase()}</span></div></td>
                <td><strong>{accountName}</strong><small>Production</small></td>
                <td><strong>{row.triggerKind.replaceAll("_", " ")}</strong><small>{row.triggerKind === "trailing_stop" ? `Trail ${row.trailOffset ?? "—"}` : `Trigger ${row.triggerPrice ?? "—"}`}{row.limitPrice ? ` · Limit ${row.limitPrice}` : ""}</small></td>
                <td className={row.side === "sell" ? "is-negative" : "is-positive"}>{row.side === "sell" ? "−" : "+"}{row.contracts}</td>
                <td><strong>{quote?.markPrice ?? "—"}</strong><small className={quote?.quoteFresh ? "is-ready" : "is-blocked"}>{quote?.quoteFresh ? "Live" : "Stale"}</small></td>
                <td><StatusBadge good={row.status === "armed"} value={row.status} /><small>{new Date(row.updatedAt).toLocaleString()}</small></td>
                <td><button aria-label={`Actions for ${row.symbol}`} className="gtt-row-action" disabled={!canTrade || !["armed", "triggered"].includes(row.status)} onClick={() => void cancel(row.id)} title="Cancel protected exit" type="button"><MoreVertical size={18} /></button></td>
              </tr>;
            })}
            {filteredRows.length === 0 ? <tr><td className="gtt-empty" colSpan={8}>No protected exits match these filters.</td></tr> : null}
          </tbody>
        </table>
      </div>

      {showCreate ? <div aria-modal="true" className="hmt-modal-backdrop" role="dialog"><form className="hmt-modal gtt-create-modal" onSubmit={(event) => void create(event)}><div className="hmt-modal__header"><div><h2>New HMT GTT</h2><p>Create a reduce-only protected exit for a Delta position.</p></div><button aria-label="Close" onClick={() => setShowCreate(false)} type="button"><X size={20} /></button></div><div className="gtt-create-grid">
        <select
          aria-label="Product"
          onChange={(event) => setProductId(event.target.value)}
          value={productId}
        >
          {products.map((product) => (
            <option key={product.productId} value={product.productId}>
              {product.symbol}
            </option>
          ))}
        </select>
        <select
          aria-label="Exit side"
          onChange={(event) => setSide(event.target.value as "buy" | "sell")}
          value={side}
        >
          <option value="sell">Sell to close long</option>
          <option value="buy">Buy to close short</option>
        </select>
        <input
          aria-label="Contracts"
          min="1"
          onChange={(event) => setContracts(event.target.value)}
          type="number"
          value={contracts}
        />
        <select
          aria-label="Protection type"
          onChange={(event) =>
            setKind(event.target.value as GttRow["triggerKind"])
          }
          value={kind}
        >
          <option value="stop_loss">Stop loss</option>
          <option value="target">Target</option>
          <option value="trailing_stop">Trailing stop</option>
        </select>
        {kind === "trailing_stop" ? (
          <input
            aria-label="Trail offset"
            onChange={(event) => setTrail(event.target.value)}
            placeholder="Trail offset"
            required
            value={trail}
          />
        ) : (
          <input
            aria-label="Trigger price"
            onChange={(event) => setTrigger(event.target.value)}
            placeholder="Trigger price"
            required
            value={trigger}
          />
        )}
        <input
          aria-label="Limit price"
          onChange={(event) => setLimit(event.target.value)}
          placeholder="Optional limit price"
          value={limit}
        />
      </div><div className="hmt-modal__actions"><button onClick={() => setShowCreate(false)} type="button">Cancel</button><button className="hmt-primary-button" disabled={!canTrade} type="submit">Arm protected exit</button></div></form></div> : null}
    </div>
  );
}

function TradingViewPage({
  user,
  csrf,
  libraryOnly = false,
}: {
  readonly user: SessionUser;
  readonly csrf: string;
  readonly libraryOnly?: boolean;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [accountId, setAccountId] = useState("");
  const [name, setName] = useState("");
  const [productId, setProductId] = useState("");
  const [contracts, setContracts] = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [reduceOnly, setReduceOnly] = useState(false);
  const [stopLossPct, setStopLossPct] = useState("2");
  const [targetPct, setTargetPct] = useState("4");
  const [trailingOffsetPct, setTrailingOffsetPct] = useState("1");
  const [strategies, setStrategies] = useState<TradingViewStrategyRow[]>([]);
  const [signals, setSignals] = useState<TradingViewSignalRow[]>([]);
  const [shownOnceUrl, setShownOnceUrl] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback((selected: string) => {
    if (!selected) {
      setStrategies([]);
      setSignals([]);
      return;
    }
    void Promise.all([
      request<{ strategies: TradingViewStrategyRow[] }>(
        `/api/v1/strategies?account_id=${encodeURIComponent(selected)}`,
      ),
      request<{ signals: TradingViewSignalRow[] }>(
        `/api/v1/strategies/signals?account_id=${encodeURIComponent(selected)}`,
      ),
    ])
      .then(([strategyRows, signalRows]) => {
        setStrategies(strategyRows.strategies);
        setSignals(signalRows.signals);
      })
      .catch(() =>
        setNotice("TradingView configuration or logs could not be loaded."),
      );
  }, []);
  useEffect(() => {
    void Promise.all([
      request<{ accounts: Account[] }>("/api/v1/accounts"),
      request<{ products: Product[] }>("/api/v1/market/products"),
    ]).then(([accountRows, productRows]) => {
      const firstAccount = accountRows.accounts[0]?.id ?? "";
      const firstProduct = productRows.products[0];
      setAccounts(accountRows.accounts);
      setProducts(productRows.products);
      setAccountId(firstAccount);
      setProductId(String(firstProduct?.productId ?? ""));
      load(firstAccount);
    });
  }, [load]);
  function resetEditor() {
    setEditingId(null);
    setShowCreate(false);
    setName("");
    setContracts("1");
    setOrderType("market");
    setReduceOnly(false);
    setStopLossPct("2");
    setTargetPct("4");
    setTrailingOffsetPct("1");
  }
  function edit(strategy: TradingViewStrategyRow) {
    setEditingId(strategy.id);
    setName(strategy.name);
    setProductId(String(strategy.productId));
    setContracts(String(strategy.contracts));
    setOrderType(strategy.orderType);
    setReduceOnly(strategy.reduceOnly);
    setStopLossPct(strategy.stopLossPct);
    setTargetPct(strategy.targetPct);
    setTrailingOffsetPct(strategy.trailingOffsetPct);
    setShowCreate(true);
    setNotice(null);
  }
  async function saveStrategy(event: FormEvent) {
    event.preventDefault();
    const product = products.find((row) => row.productId === Number(productId));
    if (!product) return;
    setBusy(true);
    setNotice(null);
    try {
      if (editingId !== null) {
        await request(
          `/api/v1/strategies/${editingId}`,
          {
            method: "PATCH",
            body: JSON.stringify({
              name,
              product_id: product.productId,
              symbol: product.symbol,
              contracts: Number(contracts),
              order_type: orderType,
              reduce_only: reduceOnly,
              stop_loss_pct: stopLossPct,
              target_pct: targetPct,
              trailing_offset_pct: trailingOffsetPct,
              protection_required: true,
            }),
          },
          csrf,
        );
        setNotice(
          "Webhook configuration updated. Its existing secret URL remains valid.",
        );
      } else {
        const strategy = await request<{ id: string }>(
          "/api/v1/strategies",
          {
            method: "POST",
            body: JSON.stringify({
              account_id: accountId,
              name,
              product_id: product.productId,
              symbol: product.symbol,
              contracts: Number(contracts),
              order_type: orderType,
              reduce_only: reduceOnly,
              stop_loss_pct: stopLossPct,
              target_pct: targetPct,
              trailing_offset_pct: trailingOffsetPct,
              protection_required: true,
              enabled: true,
            }),
          },
          csrf,
        );
        const key = await request<{ webhookPath: string }>(
          `/api/v1/strategies/${strategy.id}/webhook-key`,
          { method: "POST" },
          csrf,
        );
        setShownOnceUrl(`${window.location.origin}${key.webhookPath}`);
        setNotice(
          "Webhook configuration created. Copy the one-time URL into TradingView now.",
        );
      }
      resetEditor();
      load(accountId);
    } catch (error) {
      setNotice(
        error instanceof RequestError && error.code === "fresh_session_required"
          ? "The configuration was created, but generating its secret requires a fresh login. Sign out, sign in, then select Generate URL."
          : `Webhook ${editingId === null ? "creation" : "update"} was rejected. Confirm the Delta account, product feed, permissions, and a recent login.`,
      );
      load(accountId);
    } finally {
      setBusy(false);
    }
  }
  async function rotateKey(strategyId: string) {
    setShownOnceUrl(null);
    setNotice(null);
    try {
      const value = await request<{ webhookPath: string; shownOnce: boolean }>(
        `/api/v1/strategies/${strategyId}/webhook-key`,
        { method: "POST" },
        csrf,
      );
      setShownOnceUrl(`${window.location.origin}${value.webhookPath}`);
      setNotice(
        "Webhook URL generated. It is shown once; store it securely in TradingView now.",
      );
      load(accountId);
    } catch {
      setNotice(
        "Webhook rotation requires trading permission and a login within the last 15 minutes.",
      );
    }
  }
  async function toggleStrategy(strategy: TradingViewStrategyRow) {
    setNotice(null);
    try {
      await request(
        `/api/v1/strategies/${strategy.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({ enabled: !strategy.enabled }),
        },
        csrf,
      );
      setNotice(
        `Webhook configuration ${strategy.enabled ? "paused" : "enabled"}.`,
      );
      load(accountId);
    } catch {
      setNotice("The webhook configuration could not be updated.");
    }
  }
  async function revokeKey(strategy: TradingViewStrategyRow) {
    if (
      !window.confirm(
        `Revoke the webhook secret for ${strategy.name}? TradingView alerts using it will stop.`,
      )
    )
      return;
    setNotice(null);
    try {
      await request(
        `/api/v1/strategies/${strategy.id}/webhook-key`,
        { method: "DELETE" },
        csrf,
      );
      setNotice("Webhook secret revoked. No signal can use the old URL.");
      load(accountId);
    } catch {
      setNotice("The webhook secret could not be revoked.");
    }
  }
  const selected = accounts.find((row) => row.id === accountId);
  const canManage = user.role !== "viewer" && selected?.permission !== "view";
  return (
    <div className="tv-page">
      <div className="tv-page__title">
        <div>
          <h1>{libraryOnly ? "Strategy Library" : "TradingView Integration"}</h1>
          <p>{libraryOnly ? "Manage versioned Delta strategy configurations and mandatory protection" : "Manage secure webhook configurations for Delta Exchange India"}</p>
        </div>
        <button
          className="tv-primary-button"
          disabled={!canManage || accounts.length === 0}
          onClick={() => {
            if (showCreate) resetEditor();
            else {
              setEditingId(null);
              setShowCreate(true);
            }
          }}
          type="button"
        >
          <Plus size={18} />
          {showCreate ? "Close" : libraryOnly ? "New Strategy" : "Create Webhook"}
        </button>
      </div>
      {!libraryOnly ? <section className="tv-guide">
        <div className="tv-guide__title">
          <Info size={20} />
          <strong>How it works</strong>
        </div>
        <div className="tv-guide__body">
          <p>
            TradingView owns the signal logic. HMT Crypto validates each alert,
            applies account and risk controls, and queues eligible Live
            execution.
          </p>
          <ol>
            <li>Add and verify a Delta India account.</li>
            <li>
              Create a webhook configuration for an account, symbol, size, and
              order type.
            </li>
            <li>
              Copy the one-time URL and JSON message into the TradingView alert.
            </li>
          </ol>
          <div className="tv-endpoint">
            <strong>Webhook endpoint format</strong>
            <code>
              {window.location.origin}
              /api/v1/webhooks/tradingview/&lt;secret&gt;
            </code>
            <button
              aria-label="Copy webhook endpoint format"
              onClick={() =>
                void navigator.clipboard.writeText(
                  `${window.location.origin}/api/v1/webhooks/tradingview/<secret>`,
                )
              }
              type="button"
            >
              <Copy size={17} />
            </button>
          </div>
          <p className="tv-guide__note">
            <strong>Security:</strong> the full secret URL is displayed only
            when generated or rotated. HMT Crypto stores only its hash.
          </p>
        </div>
      </section> : (
        <section className="tv-guide"><div className="tv-guide__title"><BookOpen size={20} /><strong>Crypto strategy controls</strong></div><div className="tv-guide__body"><p>Each strategy is bound to a Delta account and contract, versioned by its audit history, and requires stop, target, and trailing protection.</p></div></section>
      )}
      {accounts.length === 0 ? (
        <section className="tv-empty">
          <KeyRound size={22} />
          <div>
            <strong>Add a Delta account first</strong>
            <p>
              A webhook must be bound to an assigned production account. Open
              Delta Accounts, create the profile, and configure its API
              credentials.
            </p>
          </div>
        </section>
      ) : null}
      {showCreate ? (
        <section className="tv-create">
          <h2>
            {editingId === null ? "Create" : "Edit"} webhook configuration
          </h2>
          <form
            className="grid gap-3 md:grid-cols-2"
            onSubmit={(event) => void saveStrategy(event)}
          >
            <label>
              Delta account
              <select
                disabled={editingId !== null}
                onChange={(event) => {
                  setAccountId(event.target.value);
                  load(event.target.value);
                }}
                required
                value={accountId}
              >
                <option value="">Select account</option>
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.profileName}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Configuration name
              <input
                maxLength={128}
                onChange={(event) => setName(event.target.value)}
                placeholder="BTC TradingView"
                required
                value={name}
              />
            </label>
            <label>
              Contract
              <select
                onChange={(event) => setProductId(event.target.value)}
                required
                value={productId}
              >
                {products.map((product) => (
                  <option key={product.productId} value={product.productId}>
                    {product.symbol}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Contracts
              <input
                min="1"
                onChange={(event) => setContracts(event.target.value)}
                required
                type="number"
                value={contracts}
              />
            </label>
            <label>
              Order type
              <select
                onChange={(event) =>
                  setOrderType(event.target.value as "market" | "limit")
                }
                value={orderType}
              >
                <option value="market">Market</option>
                <option value="limit">Limit</option>
              </select>
            </label>
            <label className="tv-checkbox">
              <input
                checked={reduceOnly}
                onChange={(event) => setReduceOnly(event.target.checked)}
                type="checkbox"
              />
              Reduce-only exit configuration
            </label>
            <label>
              Stop loss (%)
              <input
                min="0.01"
                onChange={(event) => setStopLossPct(event.target.value)}
                required
                step="0.01"
                type="number"
                value={stopLossPct}
              />
            </label>
            <label>
              Target (%)
              <input
                min="0.01"
                onChange={(event) => setTargetPct(event.target.value)}
                required
                step="0.01"
                type="number"
                value={targetPct}
              />
            </label>
            <label>
              Trailing offset (%)
              <input
                min="0.01"
                onChange={(event) => setTrailingOffsetPct(event.target.value)}
                required
                step="0.01"
                type="number"
                value={trailingOffsetPct}
              />
            </label>
            <p className="tv-guide__note">
              Protection is mandatory. A reduce-only stop, target, and trailing
              group is created only after a reconciled fill confirms quantity
              and average price.
            </p>
            <button
              className="tv-primary-button md:col-span-2"
              disabled={busy || !canManage || !accountId || !productId}
              type="submit"
            >
              {busy
                ? "Saving…"
                : editingId === null
                  ? "Create and generate URL"
                  : "Save configuration"}
            </button>
          </form>
        </section>
      ) : null}
      {notice ? (
        <p className="tv-notice" role="status">
          {notice}
        </p>
      ) : null}
      {shownOnceUrl ? (
        <section className="tv-secret">
          <div>
            <strong>Copy this URL now — it will not be shown again</strong>
            <code>{shownOnceUrl}</code>
          </div>
          <button
            onClick={() => void navigator.clipboard.writeText(shownOnceUrl)}
            type="button"
          >
            <Copy size={17} />
            Copy URL
          </button>
        </section>
      ) : null}
      <div className="tv-account-filter">
        <label>
          Delta account
          <select
            onChange={(event) => {
              setAccountId(event.target.value);
              load(event.target.value);
            }}
            value={accountId}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.profileName}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="tv-cards">
        {strategies.map((strategy) => (
          <article className="tv-card" key={strategy.id}>
            <div className="tv-card__header">
              <div>
                <span className="tv-card__key">
                  <KeyRound size={18} />
                </span>
                <strong>{strategy.name}</strong>
                <span
                  className={`tv-status ${strategy.enabled ? "is-active" : ""}`}
                >
                  {strategy.enabled ? "Active" : "Paused"}
                </span>
                <p>
                  {strategy.symbol} · {strategy.contracts} contract
                  {strategy.contracts === 1 ? "" : "s"} · {strategy.orderType} ·{" "}
                  {strategy.reduceOnly ? "Reduce only" : "Entry permitted"}
                </p>
                <p>
                  Protection: SL {strategy.stopLossPct}% · Target {strategy.targetPct}% ·
                  Trailing {strategy.trailingOffsetPct}%
                </p>
              </div>
              <div className="tv-card__actions">
                <button
                  disabled={!canManage}
                  onClick={() => edit(strategy)}
                  title="Edit webhook configuration"
                  type="button"
                >
                  <Pencil size={17} />
                </button>
                <button
                  disabled={!canManage}
                  onClick={() => void rotateKey(strategy.id)}
                  title={
                    strategy.webhookConfigured
                      ? "Regenerate webhook URL"
                      : "Generate webhook URL"
                  }
                  type="button"
                >
                  <RefreshCw size={17} />
                </button>
                <button
                  disabled={!canManage}
                  onClick={() => void toggleStrategy(strategy)}
                  title={strategy.enabled ? "Pause" : "Enable"}
                  type="button"
                >
                  {strategy.enabled ? <Pause size={17} /> : <Play size={17} />}
                </button>
                <button
                  className="is-danger"
                  disabled={!canManage || !strategy.webhookConfigured}
                  onClick={() => void revokeKey(strategy)}
                  title="Revoke webhook secret"
                  type="button"
                >
                  <Trash2 size={17} />
                </button>
              </div>
            </div>
            <div className="tv-card__secret">
              <span>Webhook secret</span>
              <strong>
                {strategy.webhookConfigured
                  ? `Configured · fingerprint ${strategy.webhookFingerprint ?? "unavailable"}`
                  : "Not generated"}
              </strong>
              <small>The secret itself is never returned after creation.</small>
            </div>
          </article>
        ))}
        {strategies.length === 0 && accounts.length > 0 ? (
          <section className="tv-empty">
            <KeyRound size={22} />
            <div>
              <strong>No webhook configurations</strong>
              <p>
                Select Create Webhook to bind a TradingView alert to this Delta
                account.
              </p>
            </div>
          </section>
        ) : null}
      </div>
      {!libraryOnly ? <Panel title="TradingView signal log">
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Received</th>
                <th>Strategy</th>
                <th>Signal</th>
                <th>Side / Price</th>
                <th>Status</th>
                <th>Reason / Intent</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((signal) => (
                <tr key={signal.id}>
                  <td>{signal.receivedAt}</td>
                  <td>{signal.strategyName}</td>
                  <td>{signal.externalSignalId}</td>
                  <td>
                    {signal.side} · {signal.limitPrice ?? "market"}
                  </td>
                  <td>{signal.status}</td>
                  <td>
                    {signal.rejectionCode ??
                      signal.intentId ??
                      "Awaiting worker"}
                  </td>
                </tr>
              ))}
              {signals.length === 0 ? (
                <tr>
                  <td colSpan={6}>No signals received.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel> : null}
      {!libraryOnly ? <section className="tv-payload">
        <strong>TradingView alert JSON</strong>
        <pre>{`{"signal_id":"{{strategy.order.id}}-{{timenow}}","side":"buy","timestamp":{{timenow}},"limit_price":null}`}</pre>
        <p>
          Signals expire after 60 seconds and duplicate signal IDs are rejected.
          Use <code>null</code> for market-order limit_price.
        </p>
      </section> : null}
    </div>
  );
}

function RiskPage({
  user,
  csrf,
}: {
  readonly user: SessionUser;
  readonly csrf: string;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [maxOrder, setMaxOrder] = useState("5000");
  const [maxPosition, setMaxPosition] = useState("10000");
  const [maxLoss, setMaxLoss] = useState("2000");
  const [maxOpen, setMaxOpen] = useState("5");
  const [maxSignals, setMaxSignals] = useState("5");
  const [usdInrRate, setUsdInrRate] = useState("100");
  const [catalogueProducts, setCatalogueProducts] = useState<Product[]>([]);
  const [policyLoaded, setPolicyLoaded] = useState(false);
  const [riskLoadError, setRiskLoadError] = useState<string | null>(null);
  const [productIds, setProductIds] = useState<number[]>([]);
  const [productSearch, setProductSearch] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [qualification, setQualification] = useState<{
    qualified: boolean;
    reason: string;
    windowHours: number;
  } | null>(null);
  useEffect(() => {
    void Promise.all([
      request<{ accounts: Account[] }>("/api/v1/accounts"),
      networkRequest<{ products: Product[] }>("/api/v1/market/products"),
    ]).then(([accountResult, marketResult]) => {
      const eligible = marketResult.products
        .filter((product) =>
          product.contractType === "perpetual_futures" &&
          product.state === "live" &&
          product.tradingStatus === "operational" &&
          !product.onlyReduceOnlyOrdersAllowed
        )
        .sort((left, right) => left.symbol.localeCompare(right.symbol));
      setAccounts(accountResult.accounts);
      setAccountId(accountResult.accounts[0]?.id ?? "");
      setCatalogueProducts(eligible);
    });
  }, []);
  useEffect(() => {
    if (!accountId) return;
    let active = true;
    setPolicyLoaded(false);
    setRiskLoadError(null);
    void networkRequest<{
      configured: boolean;
      qualified: boolean;
      qualificationReason: string;
      qualificationWindowHours: number;
      policy: null | {
        maxOrderLimitInr: string;
        maxPositionLimitInr: string;
        maxDailyLossLimitInr: string;
        maxOpenOrders: number;
        maxInflightSignals: number;
        usdInrRate: string;
        allowedProductIds: number[];
      };
    }>(`/api/v1/risk/${accountId}`).then((result) => {
      if (!active) return;
      setQualification({
        qualified: result.qualified,
        reason: result.qualificationReason,
        windowHours: result.qualificationWindowHours,
      });
      if (!result.configured || result.policy === null) return;
      setMaxOrder(result.policy.maxOrderLimitInr);
      setMaxPosition(result.policy.maxPositionLimitInr);
      setMaxLoss(result.policy.maxDailyLossLimitInr);
      setMaxOpen(String(result.policy.maxOpenOrders));
      setMaxSignals(String(result.policy.maxInflightSignals));
      setUsdInrRate(result.policy.usdInrRate);
      setProductIds(result.policy.allowedProductIds);
    }).catch(() => {
      if (active) setRiskLoadError("Could not load the saved policy. Refresh before changing the product scope.");
    }).finally(() => {
      if (active) setPolicyLoaded(true);
    });
    return () => { active = false; };
  }, [accountId]);
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      const saved = await request<{
        qualificationReset: boolean;
        policyVersion: number;
      }>(
        `/api/v1/risk/${accountId}`,
        {
          method: "PUT",
          body: JSON.stringify({
            max_order_notional: maxOrder,
            max_position_notional: maxPosition,
            max_daily_loss: maxLoss,
            max_open_orders: Number(maxOpen),
            max_inflight_signals: Number(maxSignals),
            usd_inr_rate: usdInrRate,
            allowed_product_ids: productIds,
            enabled: true,
          }),
        },
        csrf,
      );
      setNotice(saved.qualificationReset
        ? `Risk policy updated to version ${saved.policyVersion}. Its product/risk scope changed, so the previous qualification is invalid and account gates were closed. Requalify the new scope in Administration → Health & Audit before reopening gates.`
        : `Risk policy version ${saved.policyVersion} is unchanged. Existing qualification and gates were preserved.`);
      const refreshed = await networkRequest<{
        configured: boolean;
        qualified: boolean;
        qualificationReason: string;
        qualificationWindowHours: number;
      }>(`/api/v1/risk/${accountId}`);
      setQualification({
        qualified: refreshed.qualified,
        reason: refreshed.qualificationReason,
        windowHours: refreshed.qualificationWindowHours,
      });
    } catch (error) {
      const code = error instanceof RequestError ? error.code : "request_failed";
      const messages: Record<string, string> = {
        fresh_authentication_required: "Sign in again before changing the Live risk policy.",
        catalogue_not_ready: "The current Delta catalogue is not ready. Retry after the feed refreshes.",
        invalid_product_scope: "Select one or more products from the current Delta catalogue.",
        symbol_not_allowed: "One or more selected contracts are no longer active, operational perpetuals, or are restricted to reduce-only orders. Refresh the catalogue and select eligible contracts.",
        risk_scope_requires_requalification: "Use the approved INR limits, ₹100/USD rate, and five-order/five-signal caps.",
      };
      setNotice(messages[code] ?? `Risk policy was rejected (${code}).`);
    }
  }
  return (
    <Panel title="Live risk policy">
      {accountId && qualification ? (
        <div className={`m-4 rounded border px-4 py-3 ${qualification.qualified ? "border-emerald-300 bg-emerald-50 text-emerald-900" : "border-amber-300 bg-amber-50 text-amber-950"}`} role="status">
          <strong>{qualification.qualified ? "Live qualification: ready" : "Live qualification: required"}</strong>
          <p className="mt-1">{qualification.qualified
            ? `Current risk/product scope is qualified. Evidence window: ${qualification.windowHours} hour(s).`
            : `Reason: ${qualification.reason}. A change to approved products or risk limits invalidates the prior approval. Complete the ${qualification.windowHours}-hour qualification in Administration → Health & Audit, then enable account gates and the global gate.`}</p>
        </div>
      ) : null}
      <form
        className="grid gap-3 p-4 md:grid-cols-2"
        onSubmit={(e) => void save(e)}
      >
        <select
          className="rounded border px-3 py-2 md:col-span-2"
          onChange={(e) => setAccountId(e.target.value)}
          required
          value={accountId}
        >
          <option value="">Select Delta account</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.profileName}
            </option>
          ))}
        </select>
        <label>
          Maximum order notional (INR)
          <input
            className="block w-full rounded border px-3 py-2"
            onChange={(e) => setMaxOrder(e.target.value)}
            value={maxOrder}
          />
        </label>
        <label>
          Maximum aggregate exposure (INR)
          <input
            className="block w-full rounded border px-3 py-2"
            onChange={(e) => setMaxPosition(e.target.value)}
            value={maxPosition}
          />
        </label>
        <label>
          Daily-loss halt (INR)
          <input
            className="block w-full rounded border px-3 py-2"
            onChange={(e) => setMaxLoss(e.target.value)}
            value={maxLoss}
          />
        </label>
        <label>
          Maximum in-flight TradingView signals
          <input
            className="block w-full rounded border px-3 py-2"
            min="1"
            onChange={(e) => setMaxSignals(e.target.value)}
            type="number"
            value={maxSignals}
          />
        </label>
        <label>
          Fixed INR per USD
          <input
            className="block w-full rounded border px-3 py-2"
            min="1"
            onChange={(e) => setUsdInrRate(e.target.value)}
            type="number"
            value={usdInrRate}
          />
        </label>
        <p className="md:col-span-2">
          Effective USD limits: order ${Number(maxOrder || 0) / Number(usdInrRate || 1)},
          exposure ${Number(maxPosition || 0) / Number(usdInrRate || 1)}, daily loss
          ${Number(maxLoss || 0) / Number(usdInrRate || 1)}. Changing the approved
          ₹100/USD policy requires requalification.
        </p>
        <label>
          Maximum open orders
          <input
            className="block w-full rounded border px-3 py-2"
            min="1"
            onChange={(e) => setMaxOpen(e.target.value)}
            type="number"
            value={maxOpen}
          />
        </label>
        <fieldset className="rounded border p-3 md:col-span-2">
          <legend className="px-1 font-medium">Approved active Delta perpetuals</legend>
          <p className="mb-3 text-sm text-slate-600">
            Only live, operational contracts that allow new positions can be selected. Contracts that are upcoming, expired, disrupted, or reduce-only are excluded. Changing this allowlist resets the account gates and requires requalification before trading. Select all is an explicit scope expansion and may require a fresh qualification window.
          </p>
          {!policyLoaded ? <p className="mb-3 text-sm text-slate-600" role="status">Loading saved risk policy…</p> : null}
          {riskLoadError ? <p className="mb-3 text-sm text-red-700" role="alert">{riskLoadError}</p> : null}
          <div className="mb-3 flex flex-wrap items-center gap-3">
            <input
              className="min-w-64 flex-1 rounded border px-3 py-2"
              onChange={(event) => setProductSearch(event.target.value)}
              placeholder="Search perpetual contracts"
              value={productSearch}
            />
            <button
              className="rounded border px-3 py-2"
              disabled={!policyLoaded || Boolean(riskLoadError)}
              onClick={() => setProductIds(catalogueProducts.map((product) => product.productId))}
              type="button"
            >
              Select all
            </button>
            <button
              className="rounded border px-3 py-2"
              disabled={!policyLoaded || Boolean(riskLoadError)}
              onClick={() => setProductIds([])}
              type="button"
            >
              Clear
            </button>
            <span>{productIds.length} selected</span>
          </div>
          <div className="max-h-72 overflow-y-auto rounded border p-3">
          {catalogueProducts
            .filter((product) =>
              product.symbol.toLowerCase().includes(productSearch.trim().toLowerCase())
            )
            .map((product) => (
            <label className="mr-6 inline-flex items-center gap-2" key={product.productId}>
              <input
                checked={productIds.includes(product.productId)}
                disabled={!policyLoaded || Boolean(riskLoadError)}
                onChange={(event) =>
                  setProductIds((current) =>
                    event.target.checked
                      ? [...new Set([...current, product.productId])]
                      : current.filter((value) => value !== product.productId),
                  )
                }
                type="checkbox"
              />
              {product.symbol}
            </label>
          ))}
          </div>
          {catalogueProducts.length === 0 ? (
            <p className="text-red-700">No active perpetual futures are available in the current catalogue.</p>
          ) : null}
        </fieldset>
        {user.role === "admin" ? (
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white md:col-span-2"
            disabled={
              !accountId ||
              !policyLoaded ||
              Boolean(riskLoadError) ||
              catalogueProducts.length === 0 ||
              productIds.length === 0
            }
            type="submit"
          >
            Save risk policy
          </button>
        ) : (
          <p>Only an administrator can change limits.</p>
        )}
        {notice ? (
          <p className="md:col-span-2" role="status">
            {notice}
          </p>
        ) : null}
      </form>
    </Panel>
  );
}

function PerformancePage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((selected: string) => {
    if (!selected) return;
    setError(null);
    void request<PerformanceSummary>(`/api/v1/performance/${selected}`)
      .then(setSummary)
      .catch(() => {
        setSummary(null);
        setError("Fresh reconciled performance data is unavailable.");
      });
  }, []);
  useEffect(() => {
    void request<{ accounts: Account[] }>("/api/v1/accounts").then((value) => {
      const first = value.accounts[0]?.id ?? "";
      setAccounts(value.accounts);
      setAccountId(first);
      load(first);
    });
  }, [load]);
  return (
    <Panel title="Performance, funding and fees">
      <div className="flex gap-3 p-4">
        <select
          className="rounded border px-3 py-2"
          onChange={(event) => {
            setAccountId(event.target.value);
            load(event.target.value);
          }}
          value={accountId}
        >
          {accounts.map((account) => (
            <option key={account.id} value={account.id}>
              {account.profileName}
            </option>
          ))}
        </select>
        <button
          className="rounded border px-3 py-2"
          onClick={() => load(accountId)}
          type="button"
        >
          Refresh
        </button>
      </div>
      {error ? (
        <p className="px-4 pb-4" role="alert">
          {error}
        </p>
      ) : null}
      {summary ? (
        <>
          <div className="grid gap-4 p-4 md:grid-cols-5">
            <div>
              <small>Wallet net</small>
              <strong className="block">{summary.netWalletChange}</strong>
            </div>
            <div>
              <small>Funding</small>
              <strong className="block">{summary.funding}</strong>
            </div>
            <div>
              <small>Commission</small>
              <strong className="block">{summary.commission}</strong>
            </div>
            <div>
              <small>Liquidation fees</small>
              <strong className="block">{summary.liquidationFees}</strong>
            </div>
            <div>
              <small>Fills</small>
              <strong className="block">{summary.fillCount}</strong>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table>
              <thead>
                <tr>
                  <th>Wallet transaction type</th>
                  <th>Exact total</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(summary.totals).map(([kind, amount]) => (
                  <tr key={kind}>
                    <td>{kind}</td>
                    <td>{amount}</td>
                  </tr>
                ))}
                {Object.keys(summary.totals).length === 0 ? (
                  <tr>
                    <td colSpan={2}>
                      No wallet transactions in the reconciled window.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <p className="p-4">No performance snapshot.</p>
      )}
    </Panel>
  );
}

function NotificationsPage() {
  const [rows, setRows] = useState<NotificationRow[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [viewer, setViewer] = useState<Session | null>(null);
  const load = useCallback(() => {
    void request<{ notifications: NotificationRow[]; generatedAt: string }>(
      "/api/v1/notifications",
    ).then((value) => {
      setRows(value.notifications);
      setGeneratedAt(value.generatedAt);
    });
  }, []);
  useEffect(() => {
    load();
    void request<Session>("/api/v1/auth/session").then(setViewer);
  }, [load]);
  async function acknowledge(id: string) {
    if (!viewer?.csrfToken) return;
    await request(
      `/api/v1/notifications/${id}/acknowledge`,
      { method: "POST" },
      viewer.csrfToken,
    );
    load();
  }
  return (
    <Panel title="Active operational notifications">
      <div className="flex items-center gap-3 p-4">
        <span className="text-sm">Generated {generatedAt ?? "—"}</span>
        <button
          className="rounded border px-3 py-2"
          onClick={load}
          type="button"
        >
          Refresh
        </button>
      </div>
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Severity</th>
              <th>Account</th>
              <th>Code</th>
              <th>Message</th>
              <th>First seen</th>
              <th>Last seen</th>
              <th>Acknowledgement</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <strong
                    className={
                      row.severity === "critical"
                        ? "text-red-700"
                        : "text-amber-700"
                    }
                  >
                    {row.severity}
                  </strong>
                </td>
                <td>{row.accountName}</td>
                <td>{row.code}</td>
                <td>{row.message}</td>
                <td>{row.firstObservedAt}</td>
                <td>{row.observedAt}</td>
                <td>
                  {row.acknowledgedAt ??
                    (viewer?.user?.role === "admin" ? (
                      <button
                        className="rounded border px-2 py-1"
                        onClick={() => void acknowledge(row.id)}
                        type="button"
                      >
                        Acknowledge
                      </button>
                    ) : (
                      "Pending admin"
                    ))}
                </td>
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7}>No active platform findings.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

function SettingsPage({
  user,
  csrf,
}: {
  readonly user: SessionUser;
  readonly csrf: string;
}) {
  const [value, setValue] = useState<GeneralSettings | null>(null);
  const [email, setEmail] = useState("");
  const [timezone, setTimezone] =
    useState<GeneralSettings["displayTimezone"]>("UTC");
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => {
    void request<GeneralSettings>("/api/v1/settings").then((settings) => {
      setValue(settings);
      setEmail(settings.operationsContactEmail);
      setTimezone(settings.displayTimezone);
    });
  }, []);
  useEffect(load, [load]);
  async function save(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    try {
      await request(
        "/api/v1/settings",
        {
          method: "PUT",
          body: JSON.stringify({
            operations_contact_email: email,
            display_timezone: timezone,
          }),
        },
        csrf,
      );
      setNotice("General settings saved.");
      load();
    } catch {
      setNotice("Settings were rejected.");
    }
  }
  return (
    <Panel title="General settings">
      <div className="grid gap-4 border-b p-4 md:grid-cols-4">
        <div>
          <small>Venue</small>
          <strong className="block">{value?.venue ?? "—"}</strong>
        </div>
        <div>
          <small>Region</small>
          <strong className="block">{value?.region ?? "—"}</strong>
        </div>
        <div>
          <small>Environment</small>
          <strong className="block">{value?.environment ?? "—"}</strong>
        </div>
        <div>
          <small>Mode</small>
          <strong className="block">{value?.mode ?? "—"}</strong>
        </div>
      </div>
      <form
        className="grid gap-3 p-4 md:grid-cols-2"
        onSubmit={(event) => void save(event)}
      >
        <label>
          Operations contact
          <input
            className="block w-full rounded border px-3 py-2"
            disabled={user.role !== "admin"}
            onChange={(event) => setEmail(event.target.value)}
            required
            type="email"
            value={email}
          />
        </label>
        <label>
          Display timezone
          <select
            className="block w-full rounded border px-3 py-2"
            disabled={user.role !== "admin"}
            onChange={(event) =>
              setTimezone(
                event.target.value as GeneralSettings["displayTimezone"],
              )
            }
            value={timezone}
          >
            <option value="UTC">UTC</option>
            <option value="Asia/Kolkata">Asia/Kolkata</option>
          </select>
        </label>
        {user.role === "admin" ? (
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white md:col-span-2"
            type="submit"
          >
            Save general settings
          </button>
        ) : (
          <p className="md:col-span-2">
            Only administrators can edit platform settings.
          </p>
        )}
        {notice ? (
          <p className="md:col-span-2" role="status">
            {notice}
          </p>
        ) : null}
      </form>
    </Panel>
  );
}

function UsersPage({ csrf }: { readonly csrf: string }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [assignments, setAssignments] = useState<AccountAssignmentRow[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("trader");
  const [assignmentUser, setAssignmentUser] = useState("");
  const [assignmentAccount, setAssignmentAccount] = useState("");
  const [permission, setPermission] =
    useState<AccountAssignmentRow["permission"]>("trade");
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => {
    void Promise.all([
      request<{ users: UserRow[] }>("/api/v1/admin/users"),
      request<{ accounts: Account[] }>("/api/v1/accounts"),
      request<{ assignments: AccountAssignmentRow[] }>(
        "/api/v1/admin/account-assignments",
      ),
    ]).then(([userRows, accountRows, assignmentRows]) => {
      setUsers(userRows.users);
      setAccounts(accountRows.accounts);
      setAssignments(assignmentRows.assignments);
      setAssignmentUser((current) => current || userRows.users[0]?.id || "");
      setAssignmentAccount(
        (current) => current || accountRows.accounts[0]?.id || "",
      );
    });
  }, []);
  useEffect(load, [load]);
  async function invite(event: FormEvent) {
    event.preventDefault();
    await request(
      "/api/v1/admin/users/invite",
      {
        method: "POST",
        body: JSON.stringify({ email, full_name: name, role: inviteRole }),
      },
      csrf,
    );
    setEmail("");
    setName("");
    load();
  }
  async function updateUser(
    user: UserRow,
    update: { role?: Role; status?: UserRow["status"] },
  ) {
    setNotice(null);
    try {
      await request(
        `/api/v1/admin/users/${user.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            role: update.role ?? user.role,
            status: update.status ?? user.status,
          }),
        },
        csrf,
      );
      setNotice(
        "User access updated. Disabling a user revokes all application sessions.",
      );
      load();
    } catch {
      setNotice(
        "User update was rejected. You cannot remove your own administrator access.",
      );
    }
  }
  async function revokeSessions(user: UserRow) {
    setNotice(null);
    try {
      const result = await request<{ revoked: number }>(
        `/api/v1/admin/users/${user.id}/revoke-sessions`,
        { method: "POST" },
        csrf,
      );
      setNotice(`${result.revoked} active session(s) revoked.`);
    } catch {
      setNotice(
        "Session revocation was rejected. Sign in again if fresh authentication is required.",
      );
    }
  }
  async function resetPassword(user: UserRow) {
    setNotice(null);
    try {
      await request(
        `/api/v1/admin/users/${user.id}/password-reset`,
        { method: "POST" },
        csrf,
      );
      setNotice("Password-reset email requested.");
    } catch {
      setNotice("Password-reset request was rejected.");
    }
  }
  async function assign(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    try {
      await request(
        `/api/v1/accounts/${assignmentAccount}/assignments`,
        {
          method: "PUT",
          body: JSON.stringify({ user_id: assignmentUser, permission }),
        },
        csrf,
      );
      setNotice("Account assignment saved.");
      load();
    } catch {
      setNotice("Assignment was rejected.");
    }
  }
  async function remove(row: AccountAssignmentRow) {
    setNotice(null);
    try {
      await request(
        `/api/v1/accounts/${row.accountId}/assignments/${row.userId}`,
        { method: "DELETE" },
        csrf,
      );
      setNotice("Assignment removed.");
      load();
    } catch {
      setNotice(
        "Assignment cannot be removed while it is the account's last manager.",
      );
    }
  }
  return (
    <>
      <Panel title="User management">
        <form
          className="grid gap-2 p-4 md:grid-cols-4"
          onSubmit={(e) => void invite(e)}
        >
          <input
            className="rounded border px-3 py-2"
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            required
            type="email"
            value={email}
          />
          <input
            className="rounded border px-3 py-2"
            onChange={(e) => setName(e.target.value)}
            placeholder="Full name"
            required
            value={name}
          />
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setInviteRole(event.target.value as Role)}
            value={inviteRole}
          >
            <option value="viewer">Viewer</option>
            <option value="trader">Trader</option>
            <option value="admin">Admin</option>
          </select>
          <button className="rounded bg-blue-600 px-4 py-2 text-white">
            Invite user
          </button>
        </form>
        {notice ? (
          <p className="px-4 pb-4" role="status">
            {notice}
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Last login</th>
                <th>Security actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.fullName}</td>
                  <td>{u.email}</td>
                  <td>
                    <select
                      className="rounded border px-2 py-1"
                      onChange={(event) =>
                        void updateUser(u, { role: event.target.value as Role })
                      }
                      value={u.role}
                    >
                      <option value="viewer">Viewer</option>
                      <option value="trader">Trader</option>
                      <option value="admin">Admin</option>
                    </select>
                  </td>
                  <td>
                    <select
                      className="rounded border px-2 py-1"
                      onChange={(event) =>
                        void updateUser(u, {
                          status: event.target.value as UserRow["status"],
                        })
                      }
                      value={u.status}
                    >
                      <option value="invited">Invited</option>
                      <option value="active">Active</option>
                      <option value="disabled">Disabled</option>
                    </select>
                  </td>
                  <td>{u.lastLoginAt ?? "Never"}</td>
                  <td>
                    <div className="flex flex-wrap gap-2">
                      <button
                        className="text-blue-700"
                        onClick={() => void resetPassword(u)}
                        type="button"
                      >
                        Reset password
                      </button>
                      <button
                        className="text-red-700"
                        onClick={() => void revokeSessions(u)}
                        type="button"
                      >
                        Revoke sessions
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Delta account access">
        <form
          className="grid gap-3 p-4 md:grid-cols-4"
          onSubmit={(event) => void assign(event)}
        >
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setAssignmentUser(event.target.value)}
            required
            value={assignmentUser}
          >
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.email}
              </option>
            ))}
          </select>
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setAssignmentAccount(event.target.value)}
            required
            value={assignmentAccount}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.profileName}
              </option>
            ))}
          </select>
          <select
            className="rounded border px-3 py-2"
            onChange={(event) =>
              setPermission(
                event.target.value as AccountAssignmentRow["permission"],
              )
            }
            value={permission}
          >
            <option value="view">Viewer</option>
            <option value="trade">Trader</option>
            <option value="manage">Manager</option>
          </select>
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white"
            type="submit"
          >
            Save assignment
          </button>
        </form>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>User</th>
                <th>Permission</th>
                <th>Updated</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {assignments.map((row) => (
                <tr key={row.id}>
                  <td>{row.accountName}</td>
                  <td>{row.userEmail}</td>
                  <td>{row.permission}</td>
                  <td>{row.updatedAt}</td>
                  <td>
                    <button
                      className="text-red-700"
                      onClick={() => void remove(row)}
                      type="button"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
              {assignments.length === 0 ? (
                <tr>
                  <td colSpan={5}>No account assignments.</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function AuditPage({ csrf }: { readonly csrf: string }) {
  const [events, setEvents] = useState<AuditRow[]>([]);
  const [health, setHealth] = useState<{
    globalEntriesEnabled: boolean;
    globalReason: string | null;
    accounts: HealthAccount[];
  } | null>(null);
  const load = useCallback(() => {
    void request<{ events: AuditRow[] }>("/api/v1/admin/audit").then((v) =>
      setEvents(v.events),
    );
    void request<{
      globalEntriesEnabled: boolean;
      globalReason: string | null;
      accounts: HealthAccount[];
    }>("/api/v1/admin/health/platform").then(setHealth);
  }, []);
  useEffect(load, [load]);
  async function globalGate(enabled: boolean) {
    await request(
      "/api/v1/admin/live-control",
      {
        method: "PUT",
        body: JSON.stringify({
          entries_enabled: enabled,
          reason: enabled
            ? "administrator qualification approval"
            : "administrator emergency disable",
        }),
      },
      csrf,
    );
    load();
  }
  async function accountGate(accountId: string, enabled: boolean) {
    await request(
      `/api/v1/admin/accounts/${accountId}/live-control`,
      {
        method: "PUT",
        body: JSON.stringify({
          entries_enabled: enabled,
          exits_enabled: enabled,
          reason: enabled
            ? "administrator account activation approval"
            : "administrator emergency account disable",
        }),
      },
      csrf,
    );
    load();
  }
  return (
    <>
      <Panel title="Live platform health">
        <div className="flex items-center gap-3 p-4">
          <strong>
            Global entries:{" "}
            {health?.globalEntriesEnabled ? "ENABLED" : "DISABLED"}
          </strong>
          <button
            className="rounded border px-3 py-2"
            onClick={() => void globalGate(false)}
            type="button"
          >
            Emergency disable entries
          </button>
          <button
            className="rounded bg-amber-600 px-3 py-2 text-white"
            onClick={() => void globalGate(true)}
            type="button"
          >
            Enable global entry gate
          </button>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Account</th>
                <th>Worker</th>
                <th>Reconciliation</th>
                <th>Risk</th>
                <th>Gates</th>
                <th>Error</th>
                <th>Control</th>
              </tr>
            </thead>
            <tbody>
              {health?.accounts.map((a) => (
                <tr key={a.accountId}>
                  <td>{a.profileName}</td>
                  <td>{a.workerLeaseActive ? "Active" : "Missing"}</td>
                  <td>
                    {a.reconciliationState} ·{" "}
                    {a.reconciliationAgeSeconds ?? "—"}s
                  </td>
                  <td>
                    {a.riskEnabled
                      ? "Enabled"
                      : a.riskConfigured
                        ? "Disabled"
                        : "Missing"}
                  </td>
                  <td>
                    E {a.entriesEnabled ? "ON" : "OFF"} · X{" "}
                    {a.exitsEnabled ? "ON" : "OFF"}
                  </td>
                  <td>{a.lastErrorCode ?? "—"}</td>
                  <td>
                    <div className="flex gap-2">
                      <button
                        className="rounded border px-2 py-1 text-xs"
                        onClick={() => void accountGate(a.accountId, false)}
                        type="button"
                      >
                        Disable
                      </button>
                      <button
                        className="rounded bg-amber-600 px-2 py-1 text-xs text-white disabled:bg-slate-400"
                        disabled={
                          !a.workerLeaseActive ||
                          a.reconciliationState !== "ready" ||
                          !a.riskEnabled
                        }
                        onClick={() => void accountGate(a.accountId, true)}
                        type="button"
                      >
                        Enable
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Security audit">
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Target</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{e.occurredAt}</td>
                  <td>{e.action}</td>
                  <td>
                    {e.targetType}: {e.targetId}
                  </td>
                  <td>{e.outcome}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}

function DashboardPage({
  user,
  readOnlyMode,
}: {
  readonly csrf: string;
  readonly user: SessionUser;
  readonly readOnlyMode?: boolean;
}) {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const load = useCallback(() => {
    setLoading(true);
    void networkRequest<DashboardOverview>("/api/v1/dashboard/overview")
      .then(setOverview)
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 15_000);
    const refreshVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [load]);
  const totals = overview?.totals;
  return (
    <div className="hmt-page">
      <div className="hmt-page-heading">
        <div>
          <h1>Account Overview</h1>
          <p>View balances, margin, P&amp;L, and trading activity by account</p>
          <small>Last updated: {overview ? new Date(overview.observedAt).toLocaleTimeString() : "—"}</small>
        </div>
        <button className="hmt-primary-button" disabled={loading} onClick={load} type="button"><RefreshCw size={17} /> {loading ? "Refreshing…" : "Refresh"}</button>
      </div>
      <div className="hmt-dashboard-context">
        <span><ShieldCheck size={17} /> {readOnlyMode ? "Live account data · trading disabled" : "Live control"}</span>
        <span>User role: <strong>{user.role}</strong></span>
        <span>Venue: <strong>Delta Exchange India</strong></span>
      </div>
      <section className="hmt-summary-band">
        {[
          ["Available balance", money(totals?.availableBalance ?? "0"), "Across assigned accounts"],
          ["Used margin", money(totals?.usedMargin ?? "0"), "Current Delta margin"],
          ["Wallet balance", money(totals?.walletBalance ?? "0"), "Across assigned accounts"],
          ["Unrealized P&L", money(totals?.unrealizedPnl ?? "0"), "Open positions"],
          ["Active positions", String(totals?.positions ?? 0), "Open contracts"],
          ["Open orders / GTT", `${totals?.openOrders ?? 0} / ${totals?.activeGtt ?? 0}`, "Pending protection"],
        ].map(([label, value, note]) => (
          <div className="hmt-summary-cell" key={label}>
            <p className="hmt-summary-label">{label}</p>
            <p className="hmt-summary-value">{value}</p>
            <p className="hmt-summary-note">{note}</p>
          </div>
        ))}
      </section>
      <section className="hmt-account-cards">
        {overview?.accounts.map((account) => (
          <article key={account.accountId}>
            <header>
              <div><h2>{account.profileName}</h2><StatusBadge value={account.fresh ? "Fresh" : "Stale"} good={account.fresh} /></div>
              <small>{account.observedAt ? `Updated ${new Date(account.observedAt).toLocaleTimeString()}` : "Awaiting reconciliation"}</small>
            </header>
            <dl>
              <div><dt>Available</dt><dd>{money(account.availableBalance)}</dd></div>
              <div><dt>Used margin</dt><dd>{money(account.usedMargin)}</dd></div>
              <div><dt>Wallet</dt><dd>{money(account.walletBalance)}</dd></div>
              <div><dt>Unrealized P&amp;L</dt><dd>{money(account.unrealizedPnl)}</dd></div>
              <div><dt>Positions</dt><dd>{account.positionCount}</dd></div>
              <div><dt>Open orders / GTT</dt><dd>{account.openOrderCount} / {account.activeGttCount}</dd></div>
            </dl>
          </article>
        ))}
        {!loading && overview?.accounts.length === 0 ? <div className="tv-empty"><Info /><p>No assigned account has reconciled data yet.</p></div> : null}
      </section>
    </div>
  );
}

function Shell({
  session,
  onLogout,
}: {
  readonly session: Session;
  readonly onLogout: () => void;
}) {
  const user = session.user!;
  const csrf = session.csrfToken!;
  const location = useLocation();
  const navigate = useNavigate();
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [watchlistOpen, setWatchlistOpen] = useState(false);
  const closeDesktopMenus = () => {
    document
      .querySelectorAll<HTMLDetailsElement>(".hmt-nav-dropdown[open]")
      .forEach((menu) => menu.removeAttribute("open"));
  };
  useEffect(() => {
    const closeOutside = (event: PointerEvent) => {
      if (!(event.target as Element | null)?.closest(".hmt-nav-dropdown"))
        closeDesktopMenus();
    };
    const closeEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeDesktopMenus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeEscape);
    };
  }, []);
  const visible = navigation.filter(
    (item) => !item.admin || user.role === "admin",
  );
  const open = (path: string) => {
    closeDesktopMenus();
    navigate(path);
    setNavigationOpen(false);
  };
  return (
    <div className="hmt-app-shell">
      <header className="hmt-header">
        <button
          aria-label={navigationOpen ? "Close navigation" : "Open navigation"}
          className="hmt-menu-button"
          onClick={() => setNavigationOpen((value) => !value)}
          type="button"
        >
          {navigationOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
        <span className="hmt-brand">
          <span className="hmt-brand-mark">
            <Activity size={20} />
          </span>
          <span className="hmt-brand-copy">
            <strong>HelpMeTrade</strong>
            <small>Crypto</small>
          </span>
        </span>
        <nav aria-label="Primary navigation" className="hmt-primary-navigation">
          {desktopNavigation
            .filter((item) => !("admin" in item && item.admin) || user.role === "admin")
            .map((item) => {
              if (!("items" in item))
                return (
                  <button className={`hmt-nav-link ${location.pathname === item.path ? "is-active" : ""}`} key={item.path} onClick={() => open(item.path)} type="button">{item.label}</button>
                );
              const active = item.items.some((child) => child.path === location.pathname);
              return (
                <details
                  className="hmt-nav-dropdown"
                  key={item.label}
                  onToggle={(event) => {
                    if (!event.currentTarget.open) return;
                    document
                      .querySelectorAll<HTMLDetailsElement>(
                        ".hmt-nav-dropdown[open]",
                      )
                      .forEach((menu) => {
                        if (menu !== event.currentTarget)
                          menu.removeAttribute("open");
                      });
                  }}
                >
                  <summary className={`hmt-nav-link ${active ? "is-active" : ""}`}>{item.label}<ChevronDown size={14} /></summary>
                  <div>{item.items.map((child) => <button key={child.path} onClick={(event) => { open(child.path); event.currentTarget.closest("details")?.removeAttribute("open"); }} type="button">{child.label}</button>)}</div>
                </details>
              );
            })}
        </nav>
        <div className="hmt-header-actions">
          <button aria-label="Open crypto watchlist" className="hmt-mobile-watchlist" onClick={() => setWatchlistOpen(true)} type="button"><Radio size={18} /></button>
          <button aria-label="Notifications" className="hmt-icon-button" onClick={() => open("/notifications")} type="button"><Bell size={18} /></button>
          <span className="hmt-environment is-danger">DELTA INDIA · LIVE</span>
          <span className="hmt-operator">
            <span>
              {user.fullName} · {user.role}
            </span>
          </span>
          <button
            aria-label="Sign out"
            className="hmt-signout"
            onClick={onLogout}
            title="Sign out"
            type="button"
          >
            <LogOut size={17} />
            <span>Sign out</span>
          </button>
        </div>
      </header>
      <div className="hmt-workspace">
        <button
          aria-label="Close navigation"
          className={`hmt-navigation-backdrop ${navigationOpen ? "is-open" : ""}`}
          onClick={() => setNavigationOpen(false)}
          type="button"
        />
        <aside className={`hmt-sidebar ${navigationOpen ? "is-open" : ""}`}>
          <nav aria-label="Main navigation">
            {visible.map(({ path, label, icon: Icon }) => (
              <button
                className={`hmt-sidebar-link ${location.pathname === path ? "is-active" : ""}`}
                key={path}
                onClick={() => open(path)}
                type="button"
              >
                <Icon size={18} />
                <span>{label}</span>
              </button>
            ))}
          </nav>
        </aside>
        <button aria-label="Close watchlist" className={`market-rail-backdrop ${watchlistOpen ? "is-open" : ""}`} onClick={() => setWatchlistOpen(false)} type="button" />
        <CryptoMarketRail csrf={csrf} onClose={() => setWatchlistOpen(false)} open={watchlistOpen} />
        <main className="hmt-main">
          {session.readOnlyMode ? (
            <div className="mx-6 mt-5 rounded border border-blue-300 bg-blue-50 px-4 py-3 font-semibold text-blue-900">
              Live account data — trading disabled
            </div>
          ) : null}
          <div className="hmt-main-content">
            <Routes>
              <Route
                path="/dashboard"
                element={
                  <DashboardPage
                    csrf={csrf}
                    readOnlyMode={session.readOnlyMode}
                    user={user}
                  />
                }
              />
              <Route path="/market" element={<Navigate replace to="/dashboard" />} />
              <Route
                path="/accounts"
                element={<AccountsPage csrf={csrf} user={user} />}
              />
              <Route path="/trading" element={<Navigate replace to="/orders" />} />
              <Route
                path="/orders"
                element={
                  <AccountStatePage
                    csrf={csrf}
                    mode="orders"
                    readOnlyMode={session.readOnlyMode}
                    user={user}
                  />
                }
              />
              <Route
                path="/positions"
                element={
                  <AccountStatePage
                    csrf={csrf}
                    mode="positions"
                    readOnlyMode={session.readOnlyMode}
                    user={user}
                  />
                }
              />
              <Route
                path="/gtt"
                element={
                  <GttPage
                    csrf={csrf}
                    readOnlyMode={session.readOnlyMode}
                    user={user}
                  />
                }
              />
              <Route
                path="/strategies"
                element={<TradingViewPage csrf={csrf} libraryOnly user={user} />}
              />
              <Route
                path="/tradingview"
                element={<TradingViewPage csrf={csrf} user={user} />}
              />
              <Route
                path="/risk"
                element={<RiskPage csrf={csrf} user={user} />}
              />
              <Route path="/performance" element={<PerformancePage />} />
              <Route path="/notifications" element={<NotificationsPage />} />
              <Route
                path="/settings"
                element={<SettingsPage csrf={csrf} user={user} />}
              />
              <Route
                path="/users"
                element={
                  user.role === "admin" ? (
                    <UsersPage csrf={csrf} />
                  ) : (
                    <Navigate replace to="/dashboard" />
                  )
                }
              />
              <Route
                path="/health"
                element={
                  user.role === "admin" ? (
                    <AuditPage csrf={csrf} />
                  ) : (
                    <Navigate replace to="/dashboard" />
                  )
                }
              />
              <Route path="*" element={<Navigate replace to="/dashboard" />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}

function SessionSkeleton() {
  return (
    <div
      aria-label="Checking secure session"
      className="hmt-app-shell"
      role="status"
    >
      <header className="hmt-header">
        <span className="hmt-brand-copy">
          <strong>HelpMeTrade</strong>
          <small>Crypto</small>
        </span>
      </header>
      <div className="hmt-workspace">
        <aside className="hmt-sidebar hmt-skeleton-rail" />
        <main className="hmt-main">
          <div className="hmt-main-content">
            <div className="hmt-skeleton-block" />
            <div className="hmt-skeleton-block hmt-skeleton-block--large" />
          </div>
        </main>
      </div>
    </div>
  );
}

export default function PlatformApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [recoveryToken, setRecoveryToken] = useState<string | null>(() => {
    const values = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    return values.get("type") === "recovery"
      ? values.get("access_token")
      : null;
  });
  useEffect(() => {
    performance.mark("hmt-session-check-start");
    const recover = async () => {
      try {
        return await networkRequest<Session>("/api/v1/auth/refresh", {
          method: "POST",
        });
      } catch (error) {
        if (
          error instanceof RequestError &&
          ![401, 403, 503].includes(error.status)
        )
          throw error;
        return request<Session>("/api/v1/auth/session");
      }
    };
    void recover()
      .then((value) => {
        performance.mark("hmt-session-check-end");
        performance.measure(
          "hmt-session-check",
          "hmt-session-check-start",
          "hmt-session-check-end",
        );
        setSession(value);
      })
      .catch(() =>
        setSession({
          authenticated: false,
          csrfToken: null,
          readOnlyMode: true,
          user: null,
        }),
      );
  }, []);
  useEffect(() => {
    if (!session?.authenticated) return;

    let stopped = false;
    const refresh = async () => {
      try {
        const value = await networkRequest<Session>("/api/v1/auth/refresh", {
          method: "POST",
        });
        if (!stopped) setSession(value);
      } catch (error) {
        if (
          !stopped &&
          error instanceof RequestError &&
          [401, 403].includes(error.status)
        ) {
          queryClient.clear();
          setSession({
            authenticated: false,
            csrfToken: null,
            readOnlyMode: true,
            user: null,
          });
        }
        // A transient network/Auth outage must not sign an active user out.
      }
    };
    const timer = window.setInterval(refresh, 15 * 60 * 1000);
    const refreshVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [session?.authenticated]);
  if (recoveryToken !== null)
    return (
      <PasswordRecovery
        accessToken={recoveryToken}
        onComplete={() => {
          setRecoveryToken(null);
          setSession({
            authenticated: false,
            csrfToken: null,
            readOnlyMode: true,
            user: null,
          });
        }}
      />
    );
  if (session === null) return <SessionSkeleton />;
  if (
    !session.authenticated ||
    session.user === null ||
    session.csrfToken === null
  )
    return <Login onLogin={setSession} />;
  return (
    <Shell
      session={session}
      onLogout={() => {
        void request(
          "/api/v1/auth/session",
          { method: "DELETE" },
          session.csrfToken ?? "",
        ).finally(() => {
          queryClient.clear();
          setSession({
            authenticated: false,
            csrfToken: null,
            readOnlyMode: true,
            user: null,
          });
        });
      }}
    />
  );
}
