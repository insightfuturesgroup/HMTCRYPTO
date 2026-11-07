# HelpMeTrade UI Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recompose the HMT-Crypto operator console in the current HelpMeTrade production visual shell without altering trading or safety behavior.

**Architecture:** Keep `App.tsx` as the owner of session, polling and kill-command state, but extract presentation-only layout components for the header, market rail and account summary. Replace the dark/teal visual system with HelpMeTrade's blue/neutral tokens and responsive shell while preserving existing API contracts and the kill-switch component's state machine.

**Tech Stack:** React 18, TypeScript, Vite 8, Tailwind CSS 3, Lucide React.

**Spec:** `docs/superpowers/specs/2026-11-07-helpmetrade-ui-design.md`

## Global Constraints

- Do not modify the original HMT archive or project.
- Do not modify backend/API contracts, trading behavior or safety transitions.
- Never invent unavailable market/account values.
- Keep PAPER/testnet the visible default and preserve red LIVE/prod warnings.
- Add no new runtime dependency.
- Unit tests are deferred until user UI approval; run typecheck, lint, build and browser checks now.

## Review Focus

- Empty snapshot: shell renders unavailable evidence and does not imply zero balances.
- Duplicate symbols across positions/orders: market rail deduplicates by symbol and prefers position evidence.
- LIVE/prod snapshot: danger banner remains unmistakably red.
- Narrow viewport: market rail hides, navigation remains reachable, tables scroll internally.
- Kill command in progress or uncertain: the existing command reservation/status presentation remains visible and actionable.

---

### Task 1: HelpMeTrade presentation components

**Files:**
- Create: `ui/src/components/layout/HelpmeTradeHeader.tsx`
- Create: `ui/src/components/layout/CryptoMarketRail.tsx`
- Create: `ui/src/components/dashboard/AccountSummary.tsx`

**Interfaces:**
- Consumes: `DashboardSnapshot`, `DashboardView`, UTC text, loading flag and callbacks supplied by `App.tsx`.
- Produces: presentation-only `HelpmeTradeHeader`, `CryptoMarketRail` and `AccountSummary` components.

- [ ] Define shared view-item props and render the HelpMeTrade brand, four view buttons, environment badge, clock, refresh and sign-out actions.
- [ ] Build a read-only market rail that derives unique symbols from `positions` and `openOrders`, preferring position last/mark/funding evidence.
- [ ] Build a six-cell summary band for equity, balance, exposure, daily P&L, margin usage and open-position count.
- [ ] Run `npm run typecheck` and resolve component interface errors.

### Task 2: Recompose the dashboard without changing state logic

**Files:**
- Modify: `ui/src/App.tsx`

**Interfaces:**
- Consumes: components from Task 1 and the existing `controlApi`, session, polling and kill-command state.
- Produces: the HelpMeTrade shell while retaining existing `Login`, `Positions`, `Orders`, `RiskPosture`, and command reconciliation behavior.

- [ ] Export `DashboardView` for layout components and replace the dark fixed sidebar with the new top header and desktop market rail.
- [ ] Replace metric-card markup with `AccountSummary` and retain the existing view selection behavior.
- [ ] Move stream/catalogue/reconciliation/risk evidence into the rail and a compact mobile status strip.
- [ ] Preserve every alert, active-command retry, kill-switch prop and logout callback unchanged.
- [ ] Align login copy/structure with the HelpMeTrade operator card without changing submission behavior.
- [ ] Run `npm run typecheck` and inspect the diff specifically for accidental state/API changes.

### Task 3: Apply HelpMeTrade design tokens and responsive behavior

**Files:**
- Modify: `ui/src/styles.css`
- Modify: `ui/tailwind.config.ts`
- Modify: `ui/index.html`
- Create: `ui/public/favicon.svg`
- Modify: `ui/src/components/KillSwitchControl.tsx`

**Interfaces:**
- Consumes: semantic class names emitted by Tasks 1-2.
- Produces: the active HMT palette, density, navigation, summary, tables, modal treatment and responsive breakpoints.

- [ ] Replace teal/dark shell tokens with `#2563eb`, `#f6f7f9`, `#e1e5eb`, `#182230` and `#596575` while retaining semantic safety colors.
- [ ] Add 60px header, 340px rail, flat summary band, compact sticky tables, internal horizontal scrolling and mobile 44px targets.
- [ ] Restyle only the kill-switch trigger/dialog surfaces; do not alter handlers, effects, constants or conditions.
- [ ] Add the HelpMeTrade blue `H` favicon and update the browser title.
- [ ] Run `npm run lint`, `npm run typecheck`, and `npm run build`.

### Task 4: Visual acceptance and handoff

**Files:**
- Modify if needed: files from Tasks 1-3 only.

**Interfaces:**
- Consumes: built UI and local PAPER/testnet API.
- Produces: verified desktop/mobile preview and an exact changed-file handoff.

- [ ] Open the local preview, authenticate, and verify India/Testnet/PAPER dashboard rendering.
- [ ] Inspect desktop layout for brand, navigation, rail, summary band, tables, safety states and kill action.
- [ ] Inspect a mobile viewport for reachable navigation/actions and absence of page-level horizontal overflow.
- [ ] Re-run `npm run lint`, `npm run typecheck`, and `npm run build` after any visual corrections.
- [ ] Report changed files, verification evidence, deferred unit tests, and remaining external requirements.
