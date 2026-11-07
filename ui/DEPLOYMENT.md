# Dashboard deployment boundary

The browser uses only relative `/api/v1/**` control routes. Production must expose those routes on
the **same HTTPS origin** as this static application. Do not configure a browser-visible backend
origin and do not add Delta or database credentials to Vercel.

The checked-in `vercel.json` supplies conservative browser security headers. It intentionally does
not guess a backend hostname. Before a Vercel deployment can be considered operable, configure the
deployment edge or an approved reverse proxy so `/api/v1/**` reaches the authenticated FastAPI
control service without changing origin. The proxy must preserve `Set-Cookie`, CSRF and
`Idempotency-Key` headers; disable caching; enforce TLS; and allowlist only the control-service
destination. A frontend-only deployment is expected to fail closed at the login screen.

Delta API keys, API secrets, signatures, Supabase database passwords and service-role keys remain
server-only. The dashboard accepts only the operator password and receives a short-lived CSRF token
from the same-origin session API.

For the checked-in Docker Compose profile, terminate HTTPS at a host reverse proxy and forward only
to the loopback-bound nginx port. Set `HMT_API_ALLOWED_ORIGIN` to that exact HTTPS origin and keep
`HMT_API_COOKIE_SECURE=true`. Direct HTTP access to the Compose port cannot establish an operator
session by design.
