import { FormEvent, useCallback, useEffect, useState } from "react";
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
  | "market"
  | "accounts"
  | "trading"
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
  tickSize: string;
  tradingStatus: string;
  markPrice: string | null;
  bestBid: string | null;
  bestAsk: string | null;
  observedAt: string | null;
  quoteFresh: boolean;
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
  { page: "market", path: "/market", label: "Market", icon: Radio },
  {
    page: "accounts",
    path: "/accounts",
    label: "Delta Accounts",
    icon: KeyRound,
  },
  { page: "trading", path: "/trading", label: "Live Trading", icon: Target },
  {
    page: "orders",
    path: "/orders",
    label: "Orders & Fills",
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
  { label: "Market", path: "/market" },
  { label: "Delta Accounts", path: "/accounts" },
  { label: "Orders", path: "/orders" },
  { label: "Positions", path: "/positions" },
  { label: "GTT", path: "/gtt" },
  {
    label: "Trading",
    items: [
      { label: "Live Trading", path: "/trading" },
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
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, 5_000);
    return () => window.clearInterval(timer);
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

function MarketPage({ csrf }: { readonly csrf: string }) {
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
      {editingAccount !== null ? (
        <div className="hmt-modal-backdrop" role="presentation">
          <form className="hmt-modal" onSubmit={(event) => void renameAccount(event)}>
            <div className="hmt-modal__heading">
              <div><h2>Edit account</h2><p>Renaming only changes display metadata. Credentials and history remain linked.</p></div>
              <button aria-label="Close" onClick={() => setEditingAccount(null)} type="button"><X size={20} /></button>
            </div>
            <label>Account name<input autoFocus className="hmt-input" maxLength={128} minLength={1} onChange={(event) => setEditingName(event.target.value)} required value={editingName} /></label>
            <div className="hmt-modal__actions">
              <button className="hmt-secondary-button" onClick={() => setEditingAccount(null)} type="button">Cancel</button>
              <button className="hmt-primary-button" disabled={renaming || editingName.trim().length === 0} type="submit">{renaming ? "Saving…" : "Save changes"}</button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}

function TradingPage({
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
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [contracts, setContracts] = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState("");
  const [reduceOnly, setReduceOnly] = useState(false);
  const [preview, setPreview] = useState<OrderPreview | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    void Promise.all([
      request<{ accounts: Account[] }>("/api/v1/accounts"),
      request<{ products: Product[] }>("/api/v1/market/products"),
    ]).then(([a, p]) => {
      setAccounts(a.accounts);
      setAccountId(a.accounts[0]?.id ?? "");
      setProducts(p.products);
      setProductId(String(p.products[0]?.productId ?? ""));
    });
  }, []);
  async function createPreview(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    setPreview(null);
    try {
      const value = await request<OrderPreview>(
        "/api/v1/trading/previews",
        {
          method: "POST",
          body: JSON.stringify({
            account_id: accountId,
            product_id: Number(productId),
            side,
            contracts: Number(contracts),
            order_type: orderType,
            limit_price: orderType === "limit" ? limitPrice : null,
            reduce_only: reduceOnly,
          }),
        },
        csrf,
      );
      setPreview(value);
    } catch {
      setNotice(
        "Preview rejected. Check account readiness, quote freshness, position and risk limits.",
      );
    }
  }
  async function submitIntent() {
    if (preview === null) return;
    try {
      const value = await request<{ intentId: string; status: string }>(
        "/api/v1/trading/intents",
        {
          method: "POST",
          body: JSON.stringify({ preview_id: preview.previewId }),
        },
        csrf,
      );
      setNotice(
        `Live intent ${value.intentId} admitted. The fenced worker will process it.`,
      );
      setPreview(null);
    } catch {
      setNotice("Preview expired or was invalidated. Create a fresh preview.");
      setPreview(null);
    }
  }
  const selected = accounts.find((account) => account.id === accountId);
  const canTrade =
    !readOnlyMode && user.role !== "viewer" && selected?.permission !== "view";
  return (
    <>
      <Panel title="Manual Live trading">
        {readOnlyMode ? (
          <p className="mx-4 mt-4 rounded border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900">
            Qualification mode — order previews and submissions remain disabled.
          </p>
        ) : null}
        <form
          className="grid gap-3 p-4 md:grid-cols-2"
          onSubmit={(event) => void createPreview(event)}
        >
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setAccountId(event.target.value)}
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
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setProductId(event.target.value)}
            required
            value={productId}
          >
            <option value="">Select contract</option>
            {products.map((product) => (
              <option key={product.productId} value={product.productId}>
                {product.symbol} · {product.productId}
              </option>
            ))}
          </select>
          <select
            className="rounded border px-3 py-2"
            onChange={(event) => setSide(event.target.value as "buy" | "sell")}
            value={side}
          >
            <option value="buy">Buy</option>
            <option value="sell">Sell</option>
          </select>
          <input
            className="rounded border px-3 py-2"
            min="1"
            onChange={(event) => setContracts(event.target.value)}
            required
            type="number"
            value={contracts}
          />
          <select
            className="rounded border px-3 py-2"
            onChange={(event) =>
              setOrderType(event.target.value as "market" | "limit")
            }
            value={orderType}
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
          {orderType === "limit" ? (
            <input
              className="rounded border px-3 py-2"
              onChange={(event) => setLimitPrice(event.target.value)}
              placeholder="Limit price"
              required
              value={limitPrice}
            />
          ) : (
            <div className="rounded bg-slate-50 px-3 py-2 text-sm">
              Uses the fresh executable quote.
            </div>
          )}
          <label className="flex items-center gap-2">
            <input
              checked={reduceOnly}
              onChange={(event) => setReduceOnly(event.target.checked)}
              type="checkbox"
            />
            Reduce-only exit
          </label>
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white disabled:bg-slate-400"
            disabled={!canTrade || !accountId || !productId}
            type="submit"
          >
            Create 15-second preview
          </button>
          {notice ? (
            <p className="md:col-span-2" role="status">
              {notice}
            </p>
          ) : null}
        </form>
      </Panel>
      {preview ? (
        <Panel title="Confirm exact Live intent">
          <div className="grid gap-3 p-4 md:grid-cols-4">
            <div>
              <small>Product</small>
              <strong className="block">{preview.productId}</strong>
            </div>
            <div>
              <small>Validated price</small>
              <strong className="block">{preview.price}</strong>
            </div>
            <div>
              <small>Notional</small>
              <strong className="block">{preview.notional}</strong>
            </div>
            <div>
              <small>Expires</small>
              <strong className="block">{preview.expiresAt}</strong>
            </div>
            <div>
              <small>Projected aggregate exposure</small>
              <strong className="block">${preview.projectedExposure ?? "—"}</strong>
            </div>
            <div>
              <small>Risk headroom</small>
              <strong className="block">${preview.riskHeadroom ?? "—"}</strong>
            </div>
            <div>
              <small>Freshness</small>
              <strong className="block">
                Quote {preview.quoteAgeSeconds ?? "—"}s · Reconciliation{" "}
                {preview.reconciliationAgeSeconds ?? "—"}s
              </strong>
            </div>
            <div>
              <small>Required protection</small>
              <strong className="block">
                SL {preview.requiredProtection?.stopLossPct ?? "2"}% · Target{" "}
                {preview.requiredProtection?.targetPct ?? "4"}% · Trail{" "}
                {preview.requiredProtection?.trailingOffsetPct ?? "1"}%
              </strong>
            </div>
            <button
              className="rounded bg-red-700 px-4 py-2 text-white md:col-span-2"
              disabled={readOnlyMode}
              onClick={() => void submitIntent()}
              type="button"
            >
              Admit Live order intent
            </button>
            <button
              className="rounded border px-4 py-2 md:col-span-2"
              onClick={() => setPreview(null)}
              type="button"
            >
              Discard preview
            </button>
          </div>
        </Panel>
      ) : null}
    </>
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
  const [actions, setActions] = useState<LiveOrderActionRow[]>([]);
  const [modifyOrder, setModifyOrder] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [modifySize, setModifySize] = useState("");
  const [modifyPrice, setModifyPrice] = useState("");
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
      if (mode === "orders")
        void request<{ actions: LiveOrderActionRow[] }>(
          `/api/v1/trading/actions?account_id=${encodeURIComponent(selected)}`,
        )
          .then((value) => setActions(value.actions))
          .catch(() => setActions([]));
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
  const rows =
    mode === "orders"
      ? (state?.openOrders ?? [])
      : (state?.positions ?? []);
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
          ? "Cancellation queued for the fenced worker."
          : "Modification queued for the fenced worker.",
      );
      setModifyOrder(null);
      setModifySize("");
      setModifyPrice("");
      load(accountId);
    } catch {
      setError(
        "Action rejected. Refresh account state and verify the Live gates.",
      );
    }
  }
  return (
    <>
      <Panel
        title={
          mode === "orders"
            ? "Reconciled orders and fills"
            : "Reconciled positions and balances"
        }
      >
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
          <span className="self-center text-sm">
            {state
              ? `${state.fresh ? "Fresh" : "Stale"} · ${state.ageSeconds}s · reconciliation ${state.reconciliationState} · lease ${state.leaseActive ? "active" : "inactive"} · credential v${state.credentialVersion}`
              : "No state"}
          </span>
        </div>
        {error ? (
          <p className="px-4 pb-4" role="status">
            {error}
          </p>
        ) : null}
        <div className="overflow-x-auto">
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>ID</th>
                <th>Product</th>
                <th>Side / Size</th>
                <th>State</th>
                <th>Price</th>
                {mode === "orders" ? <th>Action</th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => {
                const open = mode === "orders";
                return (
                  <tr key={`${String(row.id ?? index)}-${index}`}>
                    <td>
                      {mode === "positions"
                        ? "Position"
                        : open
                          ? "Order"
                          : "Fill"}
                    </td>
                    <td>{String(row.id ?? row.order_id ?? "—")}</td>
                    <td>
                      {String(row.product_symbol ?? row.product_id ?? "—")}
                    </td>
                    <td>
                      {String(row.side ?? "")}{" "}
                      {String(row.size ?? row.contracts ?? "")}
                    </td>
                    <td>{String(row.state ?? row.role ?? "—")}</td>
                    <td>
                      {String(
                        row.limit_price ?? row.price ?? row.entry_price ?? "—",
                      )}
                    </td>
                    {mode === "orders" ? (
                      <td>
                        {open && canTrade ? (
                          <div className="flex gap-2">
                            <button
                              className="text-red-700"
                              onClick={() => void queueAction(row, "cancel")}
                              type="button"
                            >
                              Cancel
                            </button>
                            <button
                              className="text-blue-700"
                              onClick={() => {
                                setModifyOrder(row);
                                setModifySize(String(row.size ?? ""));
                                setModifyPrice(String(row.limit_price ?? ""));
                              }}
                              type="button"
                            >
                              Modify
                            </button>
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                    ) : null}
                  </tr>
                );
              })}
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={mode === "orders" ? 7 : 6}>
                    No reconciled records.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
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
        {mode === "positions" ? (
          <>
            <h3 className="border-t px-4 pt-4">Balances</h3>
            <RecordTable empty="No reconciled balances." rows={state?.balances ?? []} />
            <h3 className="border-t px-4 pt-4">Funding, fees and wallet activity</h3>
            <RecordTable
              empty="No reconciled wallet activity."
              rows={state?.walletTransactions ?? []}
            />
          </>
        ) : null}
        {mode === "orders" ? (
          <>
            <h3 className="border-t px-4 pt-4">Fills</h3>
            <RecordTable empty="No reconciled fills." rows={state?.fills ?? []} />
          </>
        ) : null}
      </Panel>
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
  return (
    <Panel title="Application-managed GTT protected exits">
      {readOnlyMode ? (
        <p className="mx-4 mt-4 rounded border border-amber-300 bg-amber-50 px-4 py-3 text-amber-900">
          Qualification mode — protected exits can be reviewed, but cannot be armed or cancelled.
        </p>
      ) : null}
      <form
        className="grid gap-3 p-4 md:grid-cols-3"
        onSubmit={(event) => void create(event)}
      >
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
        <select
          className="rounded border px-3 py-2"
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
          className="rounded border px-3 py-2"
          onChange={(event) => setSide(event.target.value as "buy" | "sell")}
          value={side}
        >
          <option value="sell">Sell to close long</option>
          <option value="buy">Buy to close short</option>
        </select>
        <input
          className="rounded border px-3 py-2"
          min="1"
          onChange={(event) => setContracts(event.target.value)}
          type="number"
          value={contracts}
        />
        <select
          className="rounded border px-3 py-2"
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
            className="rounded border px-3 py-2"
            onChange={(event) => setTrail(event.target.value)}
            placeholder="Trail offset"
            required
            value={trail}
          />
        ) : (
          <input
            className="rounded border px-3 py-2"
            onChange={(event) => setTrigger(event.target.value)}
            placeholder="Trigger price"
            required
            value={trigger}
          />
        )}
        <input
          className="rounded border px-3 py-2"
          onChange={(event) => setLimit(event.target.value)}
          placeholder="Optional limit price"
          value={limit}
        />
        <button
          className="rounded bg-blue-600 px-4 py-2 text-white disabled:bg-slate-400"
          disabled={!canTrade}
          type="submit"
        >
          Arm protected exit
        </button>
        {notice ? (
          <p className="self-center" role="status">
            {notice}
          </p>
        ) : null}
      </form>
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Exit</th>
              <th>Trigger</th>
              <th>Status</th>
              <th>Updated</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.symbol}</td>
                <td>
                  {row.side} {row.contracts}
                </td>
                <td>
                  {row.triggerKind} · {row.triggerPrice ?? row.trailOffset}
                </td>
                <td>{row.status}</td>
                <td>{row.updatedAt}</td>
                <td>
                  {["armed", "triggered"].includes(row.status) ? (
                    <button
                      className="text-red-700"
                      disabled={readOnlyMode}
                      onClick={() => void cancel(row.id)}
                      type="button"
                    >
                      Cancel
                    </button>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={6}>No protected exits.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </Panel>
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
  const [products, setProducts] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    void request<{ accounts: Account[] }>("/api/v1/accounts").then((v) => {
      setAccounts(v.accounts);
      setAccountId(v.accounts[0]?.id ?? "");
    });
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    const allowed = products
      .split(",")
      .map((v) => Number(v.trim()))
      .filter((v) => Number.isInteger(v) && v > 0);
    try {
      await request(
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
            allowed_product_ids: allowed,
            enabled: true,
          }),
        },
        csrf,
      );
      setNotice(
        "Risk policy saved. Live gates were reset and must be enabled separately.",
      );
    } catch {
      setNotice(
        "Risk policy was rejected. Confirm catalogue product IDs and limits.",
      );
    }
  }
  return (
    <Panel title="Live risk policy">
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
        <label className="md:col-span-2">
          Allowed product IDs, comma separated
          <input
            className="block w-full rounded border px-3 py-2"
            onChange={(e) => setProducts(e.target.value)}
            placeholder="27, 3136"
            value={products}
          />
        </label>
        {user.role === "admin" ? (
          <button
            className="rounded bg-blue-600 px-4 py-2 text-white md:col-span-2"
            disabled={!accountId}
            type="submit"
          >
            Save and enable risk policy
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
                          !health?.globalEntriesEnabled ||
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
    void request<DashboardOverview>("/api/v1/dashboard/overview")
      .then(setOverview)
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);
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
  const visible = navigation.filter(
    (item) => !item.admin || user.role === "admin",
  );
  const open = (path: string) => {
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
                <details className="hmt-nav-dropdown" key={item.label}>
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
              <Route path="/market" element={<MarketPage csrf={csrf} />} />
              <Route
                path="/accounts"
                element={<AccountsPage csrf={csrf} user={user} />}
              />
              <Route
                path="/trading"
                element={
                  <TradingPage
                    csrf={csrf}
                    readOnlyMode={session.readOnlyMode}
                    user={user}
                  />
                }
              />
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
    void request<Session>("/api/v1/auth/session")
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
