import {
  BookOpen,
  LayoutDashboard,
  LogOut,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  User,
  WalletCards,
} from "lucide-react";
import { type ReactNode } from "react";

export type DashboardView = "overview" | "positions" | "orders" | "risk";

export interface HelpmeTradeHeaderProps {
  readonly accountLabel?: string | null;
  readonly activeView: DashboardView;
  readonly banner: string;
  readonly dangerBanner: boolean;
  readonly loading: boolean;
  readonly onLogout: () => void;
  readonly onRefresh: () => void;
  readonly onViewChange: (view: DashboardView) => void;
  readonly safetyAction?: ReactNode;
  readonly utc: string;
}

const VIEW_ITEMS = [
  { id: "overview", label: "Dashboard", icon: LayoutDashboard },
  { id: "positions", label: "Positions", icon: WalletCards },
  { id: "orders", label: "Orders", icon: BookOpen },
  { id: "risk", label: "Risk", icon: ShieldCheck },
] as const satisfies readonly {
  readonly id: DashboardView;
  readonly label: string;
  readonly icon: typeof LayoutDashboard;
}[];

function ViewNavigation({
  activeView,
  mobile = false,
  onViewChange,
}: Pick<HelpmeTradeHeaderProps, "activeView" | "onViewChange"> & {
  readonly mobile?: boolean;
}) {
  return (
    <nav
      aria-label={mobile ? "Dashboard views" : "Primary navigation"}
      className={mobile ? "hmt-mobile-navigation" : "hmt-primary-navigation"}
    >
      {VIEW_ITEMS.map(({ id, label, icon: Icon }) => (
        <button
          aria-current={activeView === id ? "page" : undefined}
          className={`hmt-nav-link ${activeView === id ? "is-active" : ""}`}
          key={id}
          onClick={() => onViewChange(id)}
          type="button"
        >
          <Icon aria-hidden="true" size={16} />
          <span>{label}</span>
        </button>
      ))}
    </nav>
  );
}

/** HelpMeTrade web header and responsive dashboard navigation. */
export function HelpmeTradeHeader({
  accountLabel,
  activeView,
  banner,
  dangerBanner,
  loading,
  onLogout,
  onRefresh,
  onViewChange,
  safetyAction,
  utc,
}: HelpmeTradeHeaderProps) {
  return (
    <>
      <header className="hmt-header">
        <div aria-label="Helpme Trade Crypto" className="hmt-brand">
          <span aria-hidden="true" className="hmt-brand-mark">
            <TrendingUp size={21} strokeWidth={2.25} />
          </span>
          <span className="hmt-brand-copy">
            <strong>Helpme Trade</strong>
            <small>Crypto</small>
          </span>
        </div>

        <ViewNavigation activeView={activeView} onViewChange={onViewChange} />

        <div className="hmt-header-actions">
          <span
            className={`hmt-environment ${dangerBanner ? "is-danger" : ""}`}
            data-testid="environment-banner"
          >
            {banner}
          </span>
          <span aria-label={`UTC time ${utc}`} className="hmt-utc-clock">
            {utc}
          </span>
          <button
            aria-label="Refresh dashboard evidence"
            className="hmt-icon-button"
            disabled={loading}
            onClick={onRefresh}
            title="Refresh dashboard evidence"
            type="button"
          >
            <RefreshCw aria-hidden="true" className={loading ? "animate-spin" : ""} size={17} />
          </button>
          {safetyAction}
          <span className="hmt-operator" title={accountLabel ?? "Operator account"}>
            <User aria-hidden="true" size={16} />
            <span>{accountLabel ?? "Operator"}</span>
          </span>
          <button className="hmt-signout" onClick={onLogout} type="button">
            <LogOut aria-hidden="true" size={16} />
            <span>Sign out</span>
          </button>
        </div>
      </header>

      <div className="hmt-mobile-bar">
        <ViewNavigation activeView={activeView} mobile onViewChange={onViewChange} />
        <div className="hmt-mobile-meta">
          <span className={`hmt-environment ${dangerBanner ? "is-danger" : ""}`}>{banner}</span>
          <span className="hmt-mobile-utc">{utc}</span>
          <button
            aria-label="Refresh dashboard evidence"
            className="hmt-icon-button"
            disabled={loading}
            onClick={onRefresh}
            type="button"
          >
            <RefreshCw aria-hidden="true" className={loading ? "animate-spin" : ""} size={17} />
          </button>
          <button aria-label="Sign out" className="hmt-icon-button" onClick={onLogout} type="button">
            <LogOut aria-hidden="true" size={16} />
          </button>
        </div>
      </div>
    </>
  );
}
