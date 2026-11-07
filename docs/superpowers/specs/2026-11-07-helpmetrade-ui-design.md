# HelpMeTrade UI Alignment Design

## Intent

Make the HMT-Crypto operator console visibly belong to the current HelpMeTrade product while preserving the crypto control plane's fail-closed behavior. The authoritative reference is the active web shell in `hmt copy.zip`, not the obsolete permanent-navigation screenshot bundled as `public/image.png`.

## Goals

- Use the current HelpMeTrade desktop shell: 60px white top navigation, blue brand, desktop market rail, neutral workspace, compact summary band, and dense tables.
- Preserve every existing operator-session, dashboard polling, kill-command reservation, idempotency, reconciliation, and LIVE/prod warning behavior.
- Keep crypto-native data: Delta region/environment/mode, UTC clock, stream/catalogue health, funding, leverage, liquidation, margin and protection evidence.
- Remain usable on narrow screens through a compact horizontal view selector and by hiding the market rail.
- Reuse only presentation ideas and the HelpMeTrade favicon; do not import Supabase, Zerodha, or original Delta business logic.

## Non-goals

- No new order-entry or watchlist APIs.
- No fabricated prices, balances, catalogue rows, or health data.
- No authentication, API contract, backend, persistence, or trading-rule changes.
- No production deployment and no production order activity.
- Unit-test expansion remains deferred until the user's UI approval; build, type, lint, and browser verification are in scope.

## Constraints

- HMT-Crypto remains a separate project; the original archive and HMT project are read-only references.
- Unavailable exchange data must remain visibly unavailable rather than becoming zero.
- PAPER/testnet and LIVE/prod distinctions must remain prominent.
- Kill switch semantics and dialog safety transitions must not change.
- Existing React 18, TypeScript, Vite, Tailwind and Lucide dependencies are sufficient; add no UI framework.

## Approaches considered

1. **Current production shell (selected):** reproduce the active HelpMeTrade top-navigation shell and watchlist geometry around the existing HMT-Crypto state model. This is the closest match to current source and avoids obsolete navigation.
2. **Legacy screenshot shell:** retain a permanent left navigation and welcome header. This matches the bundled screenshot but not the active HelpMeTrade app.
3. **Style-only reskin:** change teal to blue without changing layout. This is fastest but would still look structurally unrelated to HelpMeTrade.

## Design

### Shell and navigation

The desktop app has a 60px white top bar with the HelpMeTrade blue trend mark, `Helpme Trade` wordmark, a small `Crypto` qualifier, and view buttons for Dashboard, Positions, Orders and Risk. Environment, UTC time, refresh, operator menu and sign-out remain accessible on the right. Below 1280px the market rail disappears; below 768px the view navigation becomes a horizontally scrollable row.

### Market rail

A 320-360px white rail lists only products already present in the authoritative dashboard snapshot (open positions or open orders). Position products can show last/mark and funding; order-only products show unavailable market evidence. Stream, catalogue, reconciliation and risk status appear as compact evidence rows. The rail is informational and contains no order-entry controls.

### Dashboard content

The page begins with an `Account Overview` heading, account label, last evidence timestamp and explicit refresh button. Five existing metrics plus open-position count render as a flat six-cell HelpMeTrade summary band. Positions, orders and safety posture retain their current fields but use compact sticky table headers, light dividers, smaller radii and minimal shadow.

### Safety controls

The environment badge is blue/amber in PAPER testnet and red in LIVE or production. The global kill switch remains a red primary safety action. Its preview, exact flatten phrase, idempotency key, uncertain-outcome handling, focus trap and status reconciliation are unchanged; only presentation classes are aligned.

### Authentication

The operator login becomes a HelpMeTrade-branded blue/white card on the neutral canvas. It still accepts only the server-side operator password and reiterates that exchange credentials never enter the browser.

## Acceptance criteria

- At desktop width the app visibly matches the active HelpMeTrade top-nav/market-rail shell.
- At mobile width there is no horizontal page overflow and all view buttons and safety actions remain reachable.
- No API request, response contract, polling interval, kill-state transition or authentication behavior changes.
- Empty/unavailable snapshots remain explicit and fail closed.
- `npm run typecheck`, `npm run lint`, and `npm run build` succeed.
- Browser review confirms login and dashboard render at desktop and mobile widths in India/Testnet/PAPER mode.
