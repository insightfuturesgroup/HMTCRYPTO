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

interface AuditRow {
  id: string;
  action: string;
  targetType: string;
  targetId: string;
  outcome: string;
  occurredAt: string;
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

function Deferred({ name }: { readonly name: string }) {
  return <Panel title={name}><div className="p-6 text-sm text-slate-600"><strong>Live module locked.</strong><p className="mt-2">This module will become available after private-stream reconciliation, live risk controls, and the final entry gate are installed. No PAPER substitute is used.</p></div></Panel>;
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

function UsersPage({ csrf }: { readonly csrf: string }) {
  const [users, setUsers] = useState<UserRow[]>([]);
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const load = useCallback(() => { void request<{ users: UserRow[] }>("/api/v1/admin/users").then((v) => setUsers(v.users)); }, []);
  useEffect(load, [load]);
  async function invite(event: FormEvent) { event.preventDefault(); await request("/api/v1/admin/users/invite", { method: "POST", body: JSON.stringify({ email, full_name: name, role: "trader" }) }, csrf); setEmail(""); setName(""); load(); }
  return <Panel title="User management"><form className="grid gap-2 p-4 md:grid-cols-[1fr_1fr_auto]" onSubmit={(e) => void invite(e)}><input className="rounded border px-3 py-2" onChange={(e) => setEmail(e.target.value)} placeholder="Email" required type="email" value={email}/><input className="rounded border px-3 py-2" onChange={(e) => setName(e.target.value)} placeholder="Full name" required value={name}/><button className="rounded bg-blue-600 px-4 py-2 text-white">Invite trader</button></form><div className="overflow-x-auto"><table><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last login</th></tr></thead><tbody>{users.map((u) => <tr key={u.id}><td>{u.fullName}</td><td>{u.email}</td><td>{u.role}</td><td>{u.status}</td><td>{u.lastLoginAt ?? "Never"}</td></tr>)}</tbody></table></div></Panel>;
}

function AuditPage() {
  const [events, setEvents] = useState<AuditRow[]>([]);
  useEffect(() => { void request<{ events: AuditRow[] }>("/api/v1/admin/audit").then((v) => setEvents(v.events)); }, []);
  return <Panel title="Security audit"><div className="overflow-x-auto"><table><thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Outcome</th></tr></thead><tbody>{events.map((e) => <tr key={e.id}><td>{e.occurredAt}</td><td>{e.action}</td><td>{e.targetType}: {e.targetId}</td><td>{e.outcome}</td></tr>)}</tbody></table></div></Panel>;
}

function Shell({ session, onLogout }: { readonly session: Session; readonly onLogout: () => void }) {
  const [page, setPage] = useState<Page>("dashboard");
  const user = session.user!;
  const csrf = session.csrfToken!;
  const visible = navigation.filter((item) => !item.admin || user.role === "admin");
  return <div className="hmt-app-shell"><header className="hmt-header"><strong>HMT Crypto</strong><span className="hmt-environment">DELTA INDIA · LIVE</span><div className="hmt-header-actions"><span className="text-sm">{user.fullName} · {user.role}</span><button onClick={onLogout} title="Sign out"><LogOut size={18}/></button></div></header><div className="hmt-workspace"><aside className="hmt-sidebar"><nav>{visible.map(({ page: target, label, icon: Icon }) => <button className={`hmt-sidebar-link ${page === target ? "is-active" : ""}`} key={target} onClick={() => setPage(target)}><Icon size={17}/><span>{label}</span></button>)}</nav></aside><main className="hmt-main"><div className="hmt-main-content">{page === "dashboard" ? <><Panel title="Live platform status"><div className="grid gap-4 p-5 md:grid-cols-3"><div><small>Trading mode</small><strong className="block">LIVE — entries locked</strong></div><div><small>User role</small><strong className="block">{user.role}</strong></div><div><small>Venue</small><strong className="block">Delta Exchange India</strong></div></div></Panel><AccountsPage csrf={csrf} user={user}/></> : null}{page === "market" ? <MarketPage/> : null}{page === "accounts" ? <AccountsPage csrf={csrf} user={user}/> : null}{page === "users" ? <UsersPage csrf={csrf}/> : null}{page === "audit" ? <AuditPage/> : null}{["trading","orders","positions","gtt","strategies","risk","performance","notifications","settings"].includes(page) ? <Deferred name={navigation.find((item) => item.page === page)?.label ?? page}/> : null}</div></main></div></div>;
}

export default function PlatformApp() {
  const [session, setSession] = useState<Session | null>(null);
  useEffect(() => { void request<Session>("/api/v1/auth/session").then(setSession).catch(() => setSession({ authenticated: false, csrfToken: null, user: null })); }, []);
  if (session === null) return <main className="grid min-h-screen place-items-center">Checking session…</main>;
  if (!session.authenticated || session.user === null || session.csrfToken === null) return <Login onLogin={setSession}/>;
  return <Shell session={session} onLogout={() => { void request("/api/v1/auth/session", { method: "DELETE" }, session.csrfToken ?? "").finally(() => setSession({ authenticated: false, csrfToken: null, user: null })); }}/>;
}
