import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  Activity,
  Bell,
  BookOpen,
  BriefcaseBusiness,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  LogOut,
  Radio,
  Settings,
  ShieldCheck,
  Target,
  Users,
} from "lucide-react";

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
  reconciliationState: "unknown" | "running" | "ready" | "degraded" | "blocked";
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
  ageSeconds: number;
  fresh: boolean;
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

async function request<T>(path: string, init?: RequestInit, csrf?: string): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body !== undefined) headers.set("Content-Type", "application/json");
  if (csrf !== undefined) headers.set("X-CSRF-Token", csrf);
  if (init?.method !== undefined && !["GET", "HEAD"].includes(init.method)) {
    headers.set("Idempotency-Key", crypto.randomUUID());
  }
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return (await response.json()) as T;
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
          <span className="hmt-auth-mark"><ShieldCheck /></span>
          <div><h1>HMT Crypto</h1><p>Delta Exchange India · Live control</p></div>
        </div>
        <div className="hmt-auth-notice">Invitation-only access. Live entries remain disabled until account qualification.</div>
        <form className="hmt-auth-form" onSubmit={(event) => void submit(event)}>
          <label>Email<input autoComplete="username" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} /></label>
          <label className="mt-4">Password<input autoComplete="current-password" onChange={(event) => setPassword(event.target.value)} required type="password" value={password} /></label>
          <button className="hmt-auth-submit" disabled={busy} type="submit">{busy ? "Signing in…" : "Sign in"}</button>
          {error ? <p className="hmt-auth-error" role="alert">{error}</p> : null}
        </form>
        <a className="mt-5 block text-center text-xs text-slate-500" href="/operator">Emergency operator access</a>
      </section>
    </main>
  );
}

const navigation: readonly { page: Page; label: string; icon: typeof Activity; admin?: boolean }[] = [
  { page: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { page: "market", label: "Market", icon: Radio },
  { page: "accounts", label: "Delta Accounts", icon: KeyRound },
  { page: "trading", label: "Live Trading", icon: Target },
  { page: "orders", label: "Orders & Fills", icon: ListChecks },
  { page: "positions", label: "Positions", icon: BriefcaseBusiness },
  { page: "gtt", label: "GTT", icon: Target },
  { page: "strategies", label: "TradingView", icon: BookOpen },
  { page: "risk", label: "Risk", icon: ShieldCheck },
  { page: "performance", label: "Performance", icon: Activity },
  { page: "notifications", label: "Notifications", icon: Bell },
  { page: "settings", label: "Settings", icon: Settings },
  { page: "users", label: "User Management", icon: Users, admin: true },
  { page: "audit", label: "Health & Audit", icon: ShieldCheck, admin: true },
];

function Panel({ title, children }: { readonly title: string; readonly children: React.ReactNode }) {
  return <section className="data-panel"><div className="data-panel__header"><h2>{title}</h2></div>{children}</section>;
}

function MarketPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [fresh, setFresh] = useState<boolean | null>(null);
  useEffect(() => {
    void request<{ snapshot: { fresh: boolean } | null; products: Product[] }>("/api/v1/market/products")
      .then((value) => { setProducts(value.products); setFresh(value.snapshot?.fresh ?? null); })
      .catch(() => { setProducts([]); setFresh(false); });
  }, []);
  return <Panel title="Delta India futures market"><div className="p-4 text-sm">Feed: <strong>{fresh === true ? "Fresh" : fresh === false ? "Unavailable or stale" : "Not published"}</strong></div><div className="overflow-x-auto"><table><thead><tr><th>Symbol</th><th>Contract</th><th>Mark</th><th>Bid</th><th>Ask</th><th>Updated</th></tr></thead><tbody>{products.map((p) => <tr key={p.productId}><td><strong>{p.symbol}</strong></td><td>{p.contractType}</td><td>{p.markPrice ?? "—"}</td><td>{p.bestBid ?? "—"}</td><td>{p.bestAsk ?? "—"}</td><td>{p.observedAt ?? "No quote"}</td></tr>)}{products.length === 0 ? <tr><td colSpan={6}>No catalogue has been published by the market worker.</td></tr> : null}</tbody></table></div></Panel>;
}

function AccountsPage({ user, csrf }: { readonly user: SessionUser; readonly csrf: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profileName, setProfileName] = useState("");
  const [credentialAccount, setCredentialAccount] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => { void request<{ accounts: Account[] }>("/api/v1/accounts").then((v) => setAccounts(v.accounts)); }, []);
  useEffect(load, [load]);
  async function create(event: FormEvent) {
    event.preventDefault();
    await request("/api/v1/accounts", { method: "POST", body: JSON.stringify({ profile_name: profileName, environment: "prod", owner_user_id: user.id }) }, csrf);
    setProfileName(""); load();
  }
  async function saveCredentials(event: FormEvent) {
    event.preventDefault();
    if (credentialAccount === null) return;
    setNotice(null);
    try {
      await request(`/api/v1/accounts/${credentialAccount}/credentials`, { method: "POST", body: JSON.stringify({ api_key: apiKey, api_secret: apiSecret }) }, csrf);
      setNotice("Credentials stored in AWS Secrets Manager. Verify them from this EC2 static IP.");
      setApiKey(""); setApiSecret(""); load();
    } catch { setNotice("Credential storage failed."); }
  }
  async function verify(accountId: string) {
    setNotice(null);
    try {
      await request(`/api/v1/accounts/${accountId}/verify`, { method: "POST" }, csrf);
      setNotice("Delta credentials and static-IP access verified. Entries remain disabled pending reconciliation.");
    } catch { setNotice("Verification was rejected or Delta was unavailable."); }
    load();
  }
  return <><Panel title="Delta accounts">{user.role === "admin" ? <form className="flex gap-2 p-4" onSubmit={(event) => void create(event)}><input className="rounded border px-3 py-2" onChange={(e) => setProfileName(e.target.value)} placeholder="Account profile name" required value={profileName}/><button className="rounded bg-blue-600 px-4 py-2 text-white" type="submit">Add Live account</button></form> : null}{notice ? <p className="px-4 pb-4 text-sm text-slate-700" role="status">{notice}</p> : null}<div className="overflow-x-auto"><table><thead><tr><th>Profile</th><th>Environment</th><th>Access</th><th>Credentials</th><th>Reconciliation</th><th>Live gates</th><th>Actions</th></tr></thead><tbody>{accounts.map((a) => <tr key={a.id}><td>{a.profileName}</td><td>{a.environment}</td><td>{a.permission}</td><td>{a.credentialState}</td><td>{a.reconciliationState}</td><td>Entries {a.entriesEnabled ? "ON" : "OFF"} · Exits {a.exitsEnabled ? "ON" : "OFF"}</td><td>{user.role === "admin" ? <div className="flex gap-2"><button className="text-blue-700" onClick={() => setCredentialAccount(a.id)} type="button">Configure</button><button className="text-blue-700 disabled:text-slate-400" disabled={!a.credentialConfigured} onClick={() => void verify(a.id)} type="button">Verify</button></div> : "—"}</td></tr>)}{accounts.length === 0 ? <tr><td colSpan={7}>No assigned Delta accounts.</td></tr> : null}</tbody></table></div>{credentialAccount !== null ? <form className="grid gap-3 border-t p-4 md:grid-cols-2" onSubmit={(event) => void saveCredentials(event)}><div className="md:col-span-2"><strong>Replace Delta API credentials</strong><p className="text-xs text-slate-500">Use a production India key with the EC2 Elastic IP whitelisted. Values are never returned.</p></div><input autoComplete="off" className="rounded border px-3 py-2" onChange={(e) => setApiKey(e.target.value)} placeholder="API key" required value={apiKey}/><input autoComplete="new-password" className="rounded border px-3 py-2" onChange={(e) => setApiSecret(e.target.value)} placeholder="API secret" required type="password" value={apiSecret}/><div className="flex gap-2 md:col-span-2"><button className="rounded bg-blue-600 px-4 py-2 text-white" type="submit">Store credentials</button><button className="rounded border px-4 py-2" onClick={() => { setCredentialAccount(null); setApiKey(""); setApiSecret(""); }} type="button">Cancel</button></div></form> : null}</Panel></>;
}

function TradingPage({ user, csrf }: { readonly user: SessionUser; readonly csrf: string }) {
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
    event.preventDefault(); setNotice(null); setPreview(null);
    try {
      const value = await request<OrderPreview>("/api/v1/trading/previews", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, product_id: Number(productId), side, contracts: Number(contracts), order_type: orderType, limit_price: orderType === "limit" ? limitPrice : null, reduce_only: reduceOnly }),
      }, csrf);
      setPreview(value);
    } catch { setNotice("Preview rejected. Check account readiness, quote freshness, position and risk limits."); }
  }
  async function submitIntent() {
    if (preview === null) return;
    try {
      const value = await request<{ intentId: string; status: string }>("/api/v1/trading/intents", { method: "POST", body: JSON.stringify({ preview_id: preview.previewId }) }, csrf);
      setNotice(`Live intent ${value.intentId} admitted. The fenced worker will process it.`); setPreview(null);
    } catch { setNotice("Preview expired or was invalidated. Create a fresh preview."); setPreview(null); }
  }
  const selected = accounts.find((account) => account.id === accountId);
  const canTrade = user.role !== "viewer" && selected?.permission !== "view";
  return <><Panel title="Manual Live trading"><form className="grid gap-3 p-4 md:grid-cols-2" onSubmit={(event) => void createPreview(event)}><select className="rounded border px-3 py-2" onChange={(event) => setAccountId(event.target.value)} required value={accountId}><option value="">Select account</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.profileName}</option>)}</select><select className="rounded border px-3 py-2" onChange={(event) => setProductId(event.target.value)} required value={productId}><option value="">Select contract</option>{products.map((product) => <option key={product.productId} value={product.productId}>{product.symbol} · {product.productId}</option>)}</select><select className="rounded border px-3 py-2" onChange={(event) => setSide(event.target.value as "buy" | "sell")} value={side}><option value="buy">Buy</option><option value="sell">Sell</option></select><input className="rounded border px-3 py-2" min="1" onChange={(event) => setContracts(event.target.value)} required type="number" value={contracts}/><select className="rounded border px-3 py-2" onChange={(event) => setOrderType(event.target.value as "market" | "limit")} value={orderType}><option value="market">Market</option><option value="limit">Limit</option></select>{orderType === "limit" ? <input className="rounded border px-3 py-2" onChange={(event) => setLimitPrice(event.target.value)} placeholder="Limit price" required value={limitPrice}/> : <div className="rounded bg-slate-50 px-3 py-2 text-sm">Uses the fresh executable quote.</div>}<label className="flex items-center gap-2"><input checked={reduceOnly} onChange={(event) => setReduceOnly(event.target.checked)} type="checkbox"/>Reduce-only exit</label><button className="rounded bg-blue-600 px-4 py-2 text-white disabled:bg-slate-400" disabled={!canTrade || !accountId || !productId} type="submit">Create 15-second preview</button>{notice ? <p className="md:col-span-2" role="status">{notice}</p> : null}</form></Panel>{preview ? <Panel title="Confirm exact Live intent"><div className="grid gap-3 p-4 md:grid-cols-4"><div><small>Product</small><strong className="block">{preview.productId}</strong></div><div><small>Validated price</small><strong className="block">{preview.price}</strong></div><div><small>Notional</small><strong className="block">{preview.notional}</strong></div><div><small>Expires</small><strong className="block">{preview.expiresAt}</strong></div><button className="rounded bg-red-700 px-4 py-2 text-white md:col-span-2" onClick={() => void submitIntent()} type="button">Admit Live order intent</button><button className="rounded border px-4 py-2 md:col-span-2" onClick={() => setPreview(null)} type="button">Discard preview</button></div></Panel> : null}</>;
}

function AccountStatePage({ mode, user, csrf }: { readonly mode: "orders" | "positions"; readonly user: SessionUser; readonly csrf: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [state, setState] = useState<AccountState | null>(null);
  const [actions, setActions] = useState<LiveOrderActionRow[]>([]);
  const [modifyOrder, setModifyOrder] = useState<Record<string, unknown> | null>(null);
  const [modifySize, setModifySize] = useState("");
  const [modifyPrice, setModifyPrice] = useState("");
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((selected: string) => {
    if (!selected) return;
    setError(null);
    void request<AccountState>(`/api/v1/accounts/${selected}/state`).then(setState).catch(() => { setState(null); setError("Reconciled account state is unavailable or stale."); });
    if (mode === "orders") void request<{ actions: LiveOrderActionRow[] }>(`/api/v1/trading/actions?account_id=${encodeURIComponent(selected)}`).then((value) => setActions(value.actions)).catch(() => setActions([]));
  }, [mode]);
  useEffect(() => { void request<{ accounts: Account[] }>("/api/v1/accounts").then((value) => { setAccounts(value.accounts); const first = value.accounts[0]?.id ?? ""; setAccountId(first); load(first); }); }, [load]);
  const rows = mode === "orders" ? [...(state?.openOrders ?? []), ...(state?.fills ?? [])] : state?.positions ?? [];
  const selected = accounts.find((account) => account.id === accountId);
  const canTrade = user.role !== "viewer" && selected?.permission !== "view";
  async function queueAction(row: Record<string, unknown>, action: "cancel" | "modify") {
    const orderId = Number(row.id ?? row.order_id);
    const productId = Number(row.product_id);
    if (!Number.isSafeInteger(orderId) || !Number.isSafeInteger(productId)) { setError("Order identity is not valid for mutation."); return; }
    try {
      await request("/api/v1/trading/actions", { method: "POST", body: JSON.stringify({ account_id: accountId, action, order_id: orderId, product_id: productId, size: action === "modify" ? Number(modifySize) : null, limit_price: action === "modify" && modifyPrice ? modifyPrice : null }) }, csrf);
      setError(action === "cancel" ? "Cancellation queued for the fenced worker." : "Modification queued for the fenced worker.");
      setModifyOrder(null); setModifySize(""); setModifyPrice(""); load(accountId);
    } catch { setError("Action rejected. Refresh account state and verify the Live gates."); }
  }
  return <><Panel title={mode === "orders" ? "Reconciled orders and fills" : "Reconciled positions and balances"}><div className="flex gap-3 p-4"><select className="rounded border px-3 py-2" onChange={(event) => { setAccountId(event.target.value); load(event.target.value); }} value={accountId}>{accounts.map((account) => <option key={account.id} value={account.id}>{account.profileName}</option>)}</select><button className="rounded border px-3 py-2" onClick={() => load(accountId)} type="button">Refresh</button><span className="self-center text-sm">{state ? `${state.fresh ? "Fresh" : "Stale"} · ${state.ageSeconds}s` : "No state"}</span></div>{error ? <p className="px-4 pb-4" role="status">{error}</p> : null}<div className="overflow-x-auto"><table><thead><tr><th>Type</th><th>ID</th><th>Product</th><th>Side / Size</th><th>State</th><th>Price</th>{mode === "orders" ? <th>Action</th> : null}</tr></thead><tbody>{rows.map((row, index) => { const open = mode === "orders" && index < (state?.openOrders.length ?? 0); return <tr key={`${String(row.id ?? index)}-${index}`}><td>{mode === "positions" ? "Position" : open ? "Order" : "Fill"}</td><td>{String(row.id ?? row.order_id ?? "—")}</td><td>{String(row.product_symbol ?? row.product_id ?? "—")}</td><td>{String(row.side ?? "")} {String(row.size ?? row.contracts ?? "")}</td><td>{String(row.state ?? row.role ?? "—")}</td><td>{String(row.limit_price ?? row.price ?? row.entry_price ?? "—")}</td>{mode === "orders" ? <td>{open && canTrade ? <div className="flex gap-2"><button className="text-red-700" onClick={() => void queueAction(row, "cancel")} type="button">Cancel</button><button className="text-blue-700" onClick={() => { setModifyOrder(row); setModifySize(String(row.size ?? "")); setModifyPrice(String(row.limit_price ?? "")); }} type="button">Modify</button></div> : "—"}</td> : null}</tr>; })}{rows.length === 0 ? <tr><td colSpan={mode === "orders" ? 7 : 6}>No reconciled records.</td></tr> : null}</tbody></table></div>{modifyOrder ? <form className="grid gap-3 border-t p-4 md:grid-cols-3" onSubmit={(event) => { event.preventDefault(); void queueAction(modifyOrder, "modify"); }}><div className="md:col-span-3"><strong>Modify order {String(modifyOrder.id ?? modifyOrder.order_id)}</strong><p className="text-xs text-slate-500">For safety, size may only stay the same or decrease and cannot be less than the already-filled size.</p></div><input className="rounded border px-3 py-2" min="1" onChange={(event) => setModifySize(event.target.value)} required type="number" value={modifySize}/><input className="rounded border px-3 py-2" onChange={(event) => setModifyPrice(event.target.value)} placeholder="Limit price (optional)" value={modifyPrice}/><div className="flex gap-2"><button className="rounded bg-blue-600 px-3 py-2 text-white" type="submit">Queue modification</button><button className="rounded border px-3 py-2" onClick={() => setModifyOrder(null)} type="button">Close</button></div></form> : null}{mode === "positions" ? <div className="border-t p-4 text-sm"><strong>Balances</strong><pre className="mt-2 overflow-auto rounded bg-slate-950 p-3 text-slate-100">{JSON.stringify(state?.balances ?? [], null, 2)}</pre></div> : null}</Panel>{mode === "orders" ? <Panel title="Cancel and modification lifecycle"><div className="overflow-x-auto"><table><thead><tr><th>Created</th><th>Action</th><th>Order</th><th>Target</th><th>Status</th><th>Outcome</th></tr></thead><tbody>{actions.map((action) => <tr key={action.id}><td>{action.createdAt}</td><td>{action.action}</td><td>{action.orderId} · {action.productId}</td><td>{action.targetSize ?? "—"} · {action.targetLimitPrice ?? "—"}</td><td>{action.status}</td><td>{action.lastErrorCode ?? action.resolvedAt ?? "Awaiting worker"}</td></tr>)}{actions.length === 0 ? <tr><td colSpan={6}>No order actions.</td></tr> : null}</tbody></table></div></Panel> : null}</>;
}

function GttPage({ user, csrf }: { readonly user: SessionUser; readonly csrf: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]); const [products, setProducts] = useState<Product[]>([]); const [accountId, setAccountId] = useState(""); const [productId, setProductId] = useState(""); const [rows, setRows] = useState<GttRow[]>([]);
  const [side, setSide] = useState<"buy" | "sell">("sell"); const [contracts, setContracts] = useState("1"); const [kind, setKind] = useState<GttRow["triggerKind"]>("stop_loss"); const [trigger, setTrigger] = useState(""); const [trail, setTrail] = useState(""); const [limit, setLimit] = useState(""); const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback((selected: string) => { if (selected) void request<{ orders: GttRow[] }>(`/api/v1/gtt?account_id=${encodeURIComponent(selected)}`).then((value) => setRows(value.orders)); }, []);
  useEffect(() => { void Promise.all([request<{ accounts: Account[] }>("/api/v1/accounts"), request<{ products: Product[] }>("/api/v1/market/products")]).then(([a, p]) => { setAccounts(a.accounts); setProducts(p.products); const first = a.accounts[0]?.id ?? ""; setAccountId(first); setProductId(String(p.products[0]?.productId ?? "")); load(first); }); }, [load]);
  async function create(event: FormEvent) { event.preventDefault(); const product = products.find((item) => item.productId === Number(productId)); try { await request("/api/v1/gtt", { method: "POST", body: JSON.stringify({ account_id: accountId, product_id: Number(productId), symbol: product?.symbol ?? String(productId), side, contracts: Number(contracts), trigger_kind: kind, trigger_price: kind === "trailing_stop" ? null : trigger, trail_offset: kind === "trailing_stop" ? trail : null, limit_price: limit || null }) }, csrf); setNotice("Protected exit armed."); load(accountId); } catch { setNotice("GTT rejected. Confirm Live readiness, fresh quotes and risk policy."); } }
  async function cancel(id: string) { await request(`/api/v1/gtt/${id}/cancel`, { method: "POST" }, csrf); load(accountId); }
  const canTrade = user.role !== "viewer" && accounts.find((account) => account.id === accountId)?.permission !== "view";
  return <Panel title="Application-managed GTT protected exits"><form className="grid gap-3 p-4 md:grid-cols-3" onSubmit={(event) => void create(event)}><select className="rounded border px-3 py-2" onChange={(event) => { setAccountId(event.target.value); load(event.target.value); }} value={accountId}>{accounts.map((account) => <option key={account.id} value={account.id}>{account.profileName}</option>)}</select><select className="rounded border px-3 py-2" onChange={(event) => setProductId(event.target.value)} value={productId}>{products.map((product) => <option key={product.productId} value={product.productId}>{product.symbol}</option>)}</select><select className="rounded border px-3 py-2" onChange={(event) => setSide(event.target.value as "buy" | "sell")} value={side}><option value="sell">Sell to close long</option><option value="buy">Buy to close short</option></select><input className="rounded border px-3 py-2" min="1" onChange={(event) => setContracts(event.target.value)} type="number" value={contracts}/><select className="rounded border px-3 py-2" onChange={(event) => setKind(event.target.value as GttRow["triggerKind"])} value={kind}><option value="stop_loss">Stop loss</option><option value="target">Target</option><option value="trailing_stop">Trailing stop</option></select>{kind === "trailing_stop" ? <input className="rounded border px-3 py-2" onChange={(event) => setTrail(event.target.value)} placeholder="Trail offset" required value={trail}/> : <input className="rounded border px-3 py-2" onChange={(event) => setTrigger(event.target.value)} placeholder="Trigger price" required value={trigger}/>}<input className="rounded border px-3 py-2" onChange={(event) => setLimit(event.target.value)} placeholder="Optional limit price" value={limit}/><button className="rounded bg-blue-600 px-4 py-2 text-white disabled:bg-slate-400" disabled={!canTrade} type="submit">Arm protected exit</button>{notice ? <p className="self-center" role="status">{notice}</p> : null}</form><div className="overflow-x-auto"><table><thead><tr><th>Symbol</th><th>Exit</th><th>Trigger</th><th>Status</th><th>Updated</th><th>Action</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.symbol}</td><td>{row.side} {row.contracts}</td><td>{row.triggerKind} · {row.triggerPrice ?? row.trailOffset}</td><td>{row.status}</td><td>{row.updatedAt}</td><td>{["armed", "triggered"].includes(row.status) ? <button className="text-red-700" onClick={() => void cancel(row.id)} type="button">Cancel</button> : "—"}</td></tr>)}{rows.length === 0 ? <tr><td colSpan={6}>No protected exits.</td></tr> : null}</tbody></table></div></Panel>;
}

function TradingViewPage({ user, csrf }: { readonly user: SessionUser; readonly csrf: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [accountId, setAccountId] = useState("");
  const [name, setName] = useState("");
  const [productId, setProductId] = useState("");
  const [contracts, setContracts] = useState("1");
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [reduceOnly, setReduceOnly] = useState(false);
  const [strategies, setStrategies] = useState<TradingViewStrategyRow[]>([]);
  const [signals, setSignals] = useState<TradingViewSignalRow[]>([]);
  const [shownOnceUrl, setShownOnceUrl] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback((selected: string) => {
    if (!selected) {
      setStrategies([]);
      setSignals([]);
      return;
    }
    void Promise.all([
      request<{ strategies: TradingViewStrategyRow[] }>(`/api/v1/strategies?account_id=${encodeURIComponent(selected)}`),
      request<{ signals: TradingViewSignalRow[] }>(`/api/v1/strategies/signals?account_id=${encodeURIComponent(selected)}`),
    ]).then(([strategyRows, signalRows]) => {
      setStrategies(strategyRows.strategies);
      setSignals(signalRows.signals);
    }).catch(() => setNotice("TradingView configuration or logs could not be loaded."));
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
  async function create(event: FormEvent) {
    event.preventDefault();
    const product = products.find((row) => row.productId === Number(productId));
    if (!product) return;
    setNotice(null);
    try {
      await request("/api/v1/strategies", {
        method: "POST",
        body: JSON.stringify({ account_id: accountId, name, product_id: product.productId, symbol: product.symbol, contracts: Number(contracts), order_type: orderType, reduce_only: reduceOnly, enabled: true }),
      }, csrf);
      setName("");
      setNotice("Strategy created. Generate its webhook URL and copy it immediately.");
      load(accountId);
    } catch {
      setNotice("Strategy creation was rejected. Confirm permissions, product freshness, and account assignment.");
    }
  }
  async function rotateKey(strategyId: string) {
    setShownOnceUrl(null);
    setNotice(null);
    try {
      const value = await request<{ webhookPath: string; shownOnce: boolean }>(`/api/v1/strategies/${strategyId}/webhook-key`, { method: "POST" }, csrf);
      setShownOnceUrl(`${window.location.origin}${value.webhookPath}`);
      setNotice("Webhook URL generated. It is shown once; store it securely in TradingView now.");
      load(accountId);
    } catch {
      setNotice("Webhook rotation requires trading permission and a login within the last 15 minutes.");
    }
  }
  const selected = accounts.find((row) => row.id === accountId);
  const canManage = user.role !== "viewer" && selected?.permission !== "view";
  return <><Panel title="TradingView Live automation"><div className="border-b p-4 text-sm"><strong>Webhook JSON</strong><pre className="mt-2 overflow-x-auto rounded bg-slate-950 p-3 text-slate-100">{`{"signal_id":"unique-id","side":"buy","timestamp":{{timenow}},"limit_price":null}`}</pre><p className="mt-2 text-slate-600">Signals expire after 60 seconds and are deduplicated. Market strategies require null limit_price; limit strategies require a positive price.</p></div><form className="grid gap-3 p-4 md:grid-cols-2" onSubmit={(event) => void create(event)}><select className="rounded border px-3 py-2" onChange={(event) => { setAccountId(event.target.value); load(event.target.value); }} required value={accountId}><option value="">Select account</option>{accounts.map((account) => <option key={account.id} value={account.id}>{account.profileName}</option>)}</select><input className="rounded border px-3 py-2" maxLength={128} onChange={(event) => setName(event.target.value)} placeholder="Strategy name" required value={name}/><select className="rounded border px-3 py-2" onChange={(event) => setProductId(event.target.value)} required value={productId}>{products.map((product) => <option key={product.productId} value={product.productId}>{product.symbol}</option>)}</select><input className="rounded border px-3 py-2" min="1" onChange={(event) => setContracts(event.target.value)} required type="number" value={contracts}/><select className="rounded border px-3 py-2" onChange={(event) => setOrderType(event.target.value as "market" | "limit")} value={orderType}><option value="market">Market</option><option value="limit">Limit</option></select><label className="flex items-center gap-2"><input checked={reduceOnly} onChange={(event) => setReduceOnly(event.target.checked)} type="checkbox"/>Reduce-only exit strategy</label><button className="rounded bg-blue-600 px-4 py-2 text-white disabled:bg-slate-400 md:col-span-2" disabled={!canManage || !accountId || !productId} type="submit">Create enabled strategy</button></form>{notice ? <p className="px-4 pb-4 text-sm" role="status">{notice}</p> : null}{shownOnceUrl ? <div className="m-4 rounded border border-amber-400 bg-amber-50 p-4"><strong>Shown once — TradingView webhook URL</strong><code className="mt-2 block break-all">{shownOnceUrl}</code><button className="mt-3 rounded border bg-white px-3 py-2" onClick={() => void navigator.clipboard.writeText(shownOnceUrl)} type="button">Copy URL</button></div> : null}<div className="overflow-x-auto"><table><thead><tr><th>Strategy</th><th>Contract</th><th>Order</th><th>Mode</th><th>Webhook</th><th>Action</th></tr></thead><tbody>{strategies.map((strategy) => <tr key={strategy.id}><td><strong>{strategy.name}</strong><br/><small>{strategy.enabled ? "Enabled" : "Disabled"}</small></td><td>{strategy.symbol} · {strategy.contracts}</td><td>{strategy.orderType}</td><td>{strategy.reduceOnly ? "Reduce only" : "Entry allowed"}</td><td>{strategy.webhookConfigured ? `Configured · ${strategy.webhookFingerprint ?? ""}` : "Not configured"}</td><td><button className="text-blue-700 disabled:text-slate-400" disabled={!canManage} onClick={() => void rotateKey(strategy.id)} type="button">{strategy.webhookConfigured ? "Rotate URL" : "Generate URL"}</button></td></tr>)}{strategies.length === 0 ? <tr><td colSpan={6}>No TradingView strategies for this account.</td></tr> : null}</tbody></table></div></Panel><Panel title="TradingView signal log"><div className="overflow-x-auto"><table><thead><tr><th>Received</th><th>Strategy</th><th>Signal</th><th>Side / Price</th><th>Status</th><th>Reason / Intent</th></tr></thead><tbody>{signals.map((signal) => <tr key={signal.id}><td>{signal.receivedAt}</td><td>{signal.strategyName}</td><td>{signal.externalSignalId}</td><td>{signal.side} · {signal.limitPrice ?? "market"}</td><td>{signal.status}</td><td>{signal.rejectionCode ?? signal.intentId ?? "Awaiting worker"}</td></tr>)}{signals.length === 0 ? <tr><td colSpan={6}>No signals received.</td></tr> : null}</tbody></table></div></Panel></>;
}

function RiskPage({ user, csrf }: { readonly user: SessionUser; readonly csrf: string }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [maxOrder, setMaxOrder] = useState("100");
  const [maxPosition, setMaxPosition] = useState("500");
  const [maxLoss, setMaxLoss] = useState("50");
  const [maxOpen, setMaxOpen] = useState("5");
  const [products, setProducts] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => { void request<{ accounts: Account[] }>("/api/v1/accounts").then((v) => { setAccounts(v.accounts); setAccountId(v.accounts[0]?.id ?? ""); }); }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    const allowed = products.split(",").map((v) => Number(v.trim())).filter((v) => Number.isInteger(v) && v > 0);
    try {
      await request(`/api/v1/risk/${accountId}`, { method: "PUT", body: JSON.stringify({ max_order_notional: maxOrder, max_position_notional: maxPosition, max_daily_loss: maxLoss, max_open_orders: Number(maxOpen), allowed_product_ids: allowed, enabled: true }) }, csrf);
      setNotice("Risk policy saved. Live gates were reset and must be enabled separately.");
    } catch { setNotice("Risk policy was rejected. Confirm catalogue product IDs and limits."); }
  }
  return <Panel title="Live risk policy"><form className="grid gap-3 p-4 md:grid-cols-2" onSubmit={(e) => void save(e)}><select className="rounded border px-3 py-2 md:col-span-2" onChange={(e) => setAccountId(e.target.value)} required value={accountId}><option value="">Select Delta account</option>{accounts.map((a) => <option key={a.id} value={a.id}>{a.profileName}</option>)}</select><label>Maximum order notional<input className="block w-full rounded border px-3 py-2" onChange={(e) => setMaxOrder(e.target.value)} value={maxOrder}/></label><label>Maximum position notional<input className="block w-full rounded border px-3 py-2" onChange={(e) => setMaxPosition(e.target.value)} value={maxPosition}/></label><label>Maximum daily loss<input className="block w-full rounded border px-3 py-2" onChange={(e) => setMaxLoss(e.target.value)} value={maxLoss}/></label><label>Maximum open orders<input className="block w-full rounded border px-3 py-2" min="1" onChange={(e) => setMaxOpen(e.target.value)} type="number" value={maxOpen}/></label><label className="md:col-span-2">Allowed product IDs, comma separated<input className="block w-full rounded border px-3 py-2" onChange={(e) => setProducts(e.target.value)} placeholder="27, 3136" value={products}/></label>{user.role === "admin" ? <button className="rounded bg-blue-600 px-4 py-2 text-white md:col-span-2" disabled={!accountId} type="submit">Save and enable risk policy</button> : <p>Only an administrator can change limits.</p>}{notice ? <p className="md:col-span-2" role="status">{notice}</p> : null}</form></Panel>;
}

function PerformancePage() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountId, setAccountId] = useState("");
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback((selected: string) => { if (!selected) return; setError(null); void request<PerformanceSummary>(`/api/v1/performance/${selected}`).then(setSummary).catch(() => { setSummary(null); setError("Fresh reconciled performance data is unavailable."); }); }, []);
  useEffect(() => { void request<{ accounts: Account[] }>("/api/v1/accounts").then((value) => { const first = value.accounts[0]?.id ?? ""; setAccounts(value.accounts); setAccountId(first); load(first); }); }, [load]);
  return <Panel title="Performance, funding and fees"><div className="flex gap-3 p-4"><select className="rounded border px-3 py-2" onChange={(event) => { setAccountId(event.target.value); load(event.target.value); }} value={accountId}>{accounts.map((account) => <option key={account.id} value={account.id}>{account.profileName}</option>)}</select><button className="rounded border px-3 py-2" onClick={() => load(accountId)} type="button">Refresh</button></div>{error ? <p className="px-4 pb-4" role="alert">{error}</p> : null}{summary ? <><div className="grid gap-4 p-4 md:grid-cols-5"><div><small>Wallet net</small><strong className="block">{summary.netWalletChange}</strong></div><div><small>Funding</small><strong className="block">{summary.funding}</strong></div><div><small>Commission</small><strong className="block">{summary.commission}</strong></div><div><small>Liquidation fees</small><strong className="block">{summary.liquidationFees}</strong></div><div><small>Fills</small><strong className="block">{summary.fillCount}</strong></div></div><div className="overflow-x-auto"><table><thead><tr><th>Wallet transaction type</th><th>Exact total</th></tr></thead><tbody>{Object.entries(summary.totals).map(([kind, amount]) => <tr key={kind}><td>{kind}</td><td>{amount}</td></tr>)}{Object.keys(summary.totals).length === 0 ? <tr><td colSpan={2}>No wallet transactions in the reconciled window.</td></tr> : null}</tbody></table></div></> : <p className="p-4">No performance snapshot.</p>}</Panel>;
}

function NotificationsPage() {
  const [rows, setRows] = useState<NotificationRow[]>([]);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const load = useCallback(() => { void request<{ notifications: NotificationRow[]; generatedAt: string }>("/api/v1/notifications").then((value) => { setRows(value.notifications); setGeneratedAt(value.generatedAt); }); }, []);
  useEffect(load, [load]);
  return <Panel title="Active operational notifications"><div className="flex items-center gap-3 p-4"><span className="text-sm">Generated {generatedAt ?? "—"}</span><button className="rounded border px-3 py-2" onClick={load} type="button">Refresh</button></div><div className="overflow-x-auto"><table><thead><tr><th>Severity</th><th>Account</th><th>Code</th><th>Message</th><th>Observed</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td><strong className={row.severity === "critical" ? "text-red-700" : "text-amber-700"}>{row.severity}</strong></td><td>{row.accountName}</td><td>{row.code}</td><td>{row.message}</td><td>{row.observedAt}</td></tr>)}{rows.length === 0 ? <tr><td colSpan={5}>No active platform findings.</td></tr> : null}</tbody></table></div></Panel>;
}

function SettingsPage({ user, csrf }: { readonly user: SessionUser; readonly csrf: string }) {
  const [value, setValue] = useState<GeneralSettings | null>(null);
  const [email, setEmail] = useState("");
  const [timezone, setTimezone] = useState<GeneralSettings["displayTimezone"]>("UTC");
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => { void request<GeneralSettings>("/api/v1/settings").then((settings) => { setValue(settings); setEmail(settings.operationsContactEmail); setTimezone(settings.displayTimezone); }); }, []);
  useEffect(load, [load]);
  async function save(event: FormEvent) { event.preventDefault(); setNotice(null); try { await request("/api/v1/settings", { method: "PUT", body: JSON.stringify({ operations_contact_email: email, display_timezone: timezone }) }, csrf); setNotice("General settings saved."); load(); } catch { setNotice("Settings were rejected."); } }
  return <Panel title="General settings"><div className="grid gap-4 border-b p-4 md:grid-cols-4"><div><small>Venue</small><strong className="block">{value?.venue ?? "—"}</strong></div><div><small>Region</small><strong className="block">{value?.region ?? "—"}</strong></div><div><small>Environment</small><strong className="block">{value?.environment ?? "—"}</strong></div><div><small>Mode</small><strong className="block">{value?.mode ?? "—"}</strong></div></div><form className="grid gap-3 p-4 md:grid-cols-2" onSubmit={(event) => void save(event)}><label>Operations contact<input className="block w-full rounded border px-3 py-2" disabled={user.role !== "admin"} onChange={(event) => setEmail(event.target.value)} required type="email" value={email}/></label><label>Display timezone<select className="block w-full rounded border px-3 py-2" disabled={user.role !== "admin"} onChange={(event) => setTimezone(event.target.value as GeneralSettings["displayTimezone"])} value={timezone}><option value="UTC">UTC</option><option value="Asia/Kolkata">Asia/Kolkata</option></select></label>{user.role === "admin" ? <button className="rounded bg-blue-600 px-4 py-2 text-white md:col-span-2" type="submit">Save general settings</button> : <p className="md:col-span-2">Only administrators can edit platform settings.</p>}{notice ? <p className="md:col-span-2" role="status">{notice}</p> : null}</form></Panel>;
}

function UsersPage({ csrf }: { readonly csrf: string }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [assignments, setAssignments] = useState<AccountAssignmentRow[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [assignmentUser, setAssignmentUser] = useState("");
  const [assignmentAccount, setAssignmentAccount] = useState("");
  const [permission, setPermission] = useState<AccountAssignmentRow["permission"]>("trade");
  const [notice, setNotice] = useState<string | null>(null);
  const load = useCallback(() => { void Promise.all([request<{ users: UserRow[] }>("/api/v1/admin/users"), request<{ accounts: Account[] }>("/api/v1/accounts"), request<{ assignments: AccountAssignmentRow[] }>("/api/v1/admin/account-assignments")]).then(([userRows, accountRows, assignmentRows]) => { setUsers(userRows.users); setAccounts(accountRows.accounts); setAssignments(assignmentRows.assignments); setAssignmentUser((current) => current || userRows.users[0]?.id || ""); setAssignmentAccount((current) => current || accountRows.accounts[0]?.id || ""); }); }, []);
  useEffect(load, [load]);
  async function invite(event: FormEvent) { event.preventDefault(); await request("/api/v1/admin/users/invite", { method: "POST", body: JSON.stringify({ email, full_name: name, role: "trader" }) }, csrf); setEmail(""); setName(""); load(); }
  async function assign(event: FormEvent) { event.preventDefault(); setNotice(null); try { await request(`/api/v1/accounts/${assignmentAccount}/assignments`, { method: "PUT", body: JSON.stringify({ user_id: assignmentUser, permission }) }, csrf); setNotice("Account assignment saved."); load(); } catch { setNotice("Assignment was rejected."); } }
  async function remove(row: AccountAssignmentRow) { setNotice(null); try { await request(`/api/v1/accounts/${row.accountId}/assignments/${row.userId}`, { method: "DELETE" }, csrf); setNotice("Assignment removed."); load(); } catch { setNotice("Assignment cannot be removed while it is the account's last manager."); } }
  return <><Panel title="User management"><form className="grid gap-2 p-4 md:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => void invite(e)}><input className="rounded border px-3 py-2" onChange={(e) => setEmail(e.target.value)} placeholder="Email" required type="email" value={email}/><input className="rounded border px-3 py-2" onChange={(e) => setName(e.target.value)} placeholder="Full name" required value={name}/><button className="rounded bg-blue-600 px-4 py-2 text-white">Invite trader</button></form><div className="overflow-x-auto"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last login</th></tr></thead><tbody>{users.map((u) => <tr key={u.id}><td>{u.fullName}</td><td>{u.email}</td><td>{u.role}</td><td>{u.status}</td><td>{u.lastLoginAt ?? "Never"}</td></tr>)}</tbody></table></div></Panel><Panel title="Delta account access"><form className="grid gap-3 p-4 md:grid-cols-4" onSubmit={(event) => void assign(event)}><select className="rounded border px-3 py-2" onChange={(event) => setAssignmentUser(event.target.value)} required value={assignmentUser}>{users.map((user) => <option key={user.id} value={user.id}>{user.email}</option>)}</select><select className="rounded border px-3 py-2" onChange={(event) => setAssignmentAccount(event.target.value)} required value={assignmentAccount}>{accounts.map((account) => <option key={account.id} value={account.id}>{account.profileName}</option>)}</select><select className="rounded border px-3 py-2" onChange={(event) => setPermission(event.target.value as AccountAssignmentRow["permission"])} value={permission}><option value="view">Viewer</option><option value="trade">Trader</option><option value="manage">Manager</option></select><button className="rounded bg-blue-600 px-4 py-2 text-white" type="submit">Save assignment</button></form>{notice ? <p className="px-4 pb-4" role="status">{notice}</p> : null}<div className="overflow-x-auto"><table><thead><tr><th>Account</th><th>User</th><th>Permission</th><th>Updated</th><th>Action</th></tr></thead><tbody>{assignments.map((row) => <tr key={row.id}><td>{row.accountName}</td><td>{row.userEmail}</td><td>{row.permission}</td><td>{row.updatedAt}</td><td><button className="text-red-700" onClick={() => void remove(row)} type="button">Remove</button></td></tr>)}{assignments.length === 0 ? <tr><td colSpan={5}>No account assignments.</td></tr> : null}</tbody></table></div></Panel></>;
}

function AuditPage({ csrf }: { readonly csrf: string }) {
  const [events, setEvents] = useState<AuditRow[]>([]);
  const [health, setHealth] = useState<{ globalEntriesEnabled: boolean; globalReason: string | null; accounts: HealthAccount[] } | null>(null);
  const load = useCallback(() => { void request<{ events: AuditRow[] }>("/api/v1/admin/audit").then((v) => setEvents(v.events)); void request<{ globalEntriesEnabled: boolean; globalReason: string | null; accounts: HealthAccount[] }>("/api/v1/admin/health/platform").then(setHealth); }, []);
  useEffect(load, [load]);
  async function globalGate(enabled: boolean) { await request("/api/v1/admin/live-control", { method: "PUT", body: JSON.stringify({ entries_enabled: enabled, reason: enabled ? "administrator qualification approval" : "administrator emergency disable" }) }, csrf); load(); }
  async function accountGate(accountId: string, enabled: boolean) {
    await request(`/api/v1/admin/accounts/${accountId}/live-control`, { method: "PUT", body: JSON.stringify({ entries_enabled: enabled, exits_enabled: enabled, reason: enabled ? "administrator account activation approval" : "administrator emergency account disable" }) }, csrf);
    load();
  }
  return <><Panel title="Live platform health"><div className="flex items-center gap-3 p-4"><strong>Global entries: {health?.globalEntriesEnabled ? "ENABLED" : "DISABLED"}</strong><button className="rounded border px-3 py-2" onClick={() => void globalGate(false)} type="button">Emergency disable entries</button><button className="rounded bg-amber-600 px-3 py-2 text-white" onClick={() => void globalGate(true)} type="button">Enable global entry gate</button></div><div className="overflow-x-auto"><table><thead><tr><th>Account</th><th>Worker</th><th>Reconciliation</th><th>Risk</th><th>Gates</th><th>Error</th><th>Control</th></tr></thead><tbody>{health?.accounts.map((a) => <tr key={a.accountId}><td>{a.profileName}</td><td>{a.workerLeaseActive ? "Active" : "Missing"}</td><td>{a.reconciliationState} · {a.reconciliationAgeSeconds ?? "—"}s</td><td>{a.riskEnabled ? "Enabled" : a.riskConfigured ? "Disabled" : "Missing"}</td><td>E {a.entriesEnabled ? "ON" : "OFF"} · X {a.exitsEnabled ? "ON" : "OFF"}</td><td>{a.lastErrorCode ?? "—"}</td><td><div className="flex gap-2"><button className="rounded border px-2 py-1 text-xs" onClick={() => void accountGate(a.accountId, false)} type="button">Disable</button><button className="rounded bg-amber-600 px-2 py-1 text-xs text-white disabled:bg-slate-400" disabled={!health?.globalEntriesEnabled || !a.workerLeaseActive || a.reconciliationState !== "ready" || !a.riskEnabled} onClick={() => void accountGate(a.accountId, true)} type="button">Enable</button></div></td></tr>)}</tbody></table></div></Panel><Panel title="Security audit"><div className="overflow-x-auto"><table><thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead><tbody>{events.map((e) => <tr key={e.id}><td>{e.occurredAt}</td><td>{e.action}</td><td>{e.targetType}: {e.targetId}</td><td>{e.outcome}</td></tr>)}</tbody></table></div></Panel></>;
}

function Shell({ session, onLogout }: { readonly session: Session; readonly onLogout: () => void }) {
  const [page, setPage] = useState<Page>("dashboard");
  const user = session.user!;
  const csrf = session.csrfToken!;
  const visible = navigation.filter((item) => !item.admin || user.role === "admin");
  return <div className="hmt-app-shell"><header className="hmt-header"><strong>HMT Crypto</strong><span className="hmt-environment">DELTA INDIA · LIVE</span><div className="hmt-header-actions"><span className="text-sm">{user.fullName} · {user.role}</span><button onClick={onLogout} title="Sign out"><LogOut size={18}/></button></div></header><div className="hmt-workspace"><aside className="hmt-sidebar"><nav>{visible.map(({ page: target, label, icon: Icon }) => <button className={`hmt-sidebar-link ${page === target ? "is-active" : ""}`} key={target} onClick={() => setPage(target)}><Icon size={17}/><span>{label}</span></button>)}</nav></aside><main className="hmt-main"><div className="hmt-main-content">{page === "dashboard" ? <><Panel title="Live platform status"><div className="grid gap-4 p-5 md:grid-cols-3"><div><small>Trading mode</small><strong className="block">LIVE — entries locked</strong></div><div><small>User role</small><strong className="block">{user.role}</strong></div><div><small>Venue</small><strong className="block">Delta Exchange India</strong></div></div></Panel><AccountsPage csrf={csrf} user={user}/></> : null}{page === "market" ? <MarketPage/> : null}{page === "accounts" ? <AccountsPage csrf={csrf} user={user}/> : null}{page === "trading" ? <TradingPage csrf={csrf} user={user}/> : null}{page === "orders" ? <AccountStatePage csrf={csrf} mode="orders" user={user}/> : null}{page === "positions" ? <AccountStatePage csrf={csrf} mode="positions" user={user}/> : null}{page === "gtt" ? <GttPage csrf={csrf} user={user}/> : null}{page === "strategies" ? <TradingViewPage csrf={csrf} user={user}/> : null}{page === "risk" ? <RiskPage csrf={csrf} user={user}/> : null}{page === "performance" ? <PerformancePage/> : null}{page === "notifications" ? <NotificationsPage/> : null}{page === "settings" ? <SettingsPage csrf={csrf} user={user}/> : null}{page === "users" ? <UsersPage csrf={csrf}/> : null}{page === "audit" ? <AuditPage csrf={csrf}/> : null}</div></main></div></div>;
}

export default function PlatformApp() {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { void request<Session>("/api/v1/auth/session").then(setSession).catch(() => setSession({ authenticated: false, csrfToken: null, user: null })); }, []);
  if (session === null) return <main className="grid min-h-screen place-items-center">Checking session…</main>;
  if (!session.authenticated || session.user === null || session.csrfToken === null) return <Login onLogin={setSession}/>;
  return <Shell session={session} onLogout={() => { void request("/api/v1/auth/session", { method: "DELETE" }, session.csrfToken ?? "").finally(() => setSession({ authenticated: false, csrfToken: null, user: null })); }}/>;
}
