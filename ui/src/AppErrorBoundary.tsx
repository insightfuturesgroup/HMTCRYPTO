import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

const RELOAD_GUARD = "hmt-ui-recovery-attempt";

export class AppErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Application render failed", error, info.componentStack);
  }

  private reload = (): void => {
    sessionStorage.removeItem(RELOAD_GUARD);
    window.location.reload();
  };

  render(): ReactNode {
    if (this.state.error === null) return this.props.children;

    return (
      <main className="app-recovery" role="alert">
        <section className="app-recovery__panel">
          <p className="app-recovery__eyebrow">Connection interrupted</p>
          <h1>HelpMeTrade could not display this page.</h1>
          <p>Your trading session is unchanged. Reload to reconnect to the latest application version.</p>
          <button type="button" onClick={this.reload}>Reload application</button>
        </section>
      </main>
    );
  }
}

export function installChunkRecovery(): void {
  window.addEventListener("unhandledrejection", (event) => {
    const message = event.reason instanceof Error ? event.reason.message : String(event.reason ?? "");
    const chunkFailed = /Failed to fetch dynamically imported module|Importing a module script failed|Loading chunk/i.test(message);
    if (!chunkFailed || sessionStorage.getItem(RELOAD_GUARD) === "1") return;
    sessionStorage.setItem(RELOAD_GUARD, "1");
    window.location.reload();
  });
}
