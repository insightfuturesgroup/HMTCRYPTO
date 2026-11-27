# Operator-only Supabase hardening

No trading gate changes. Agent testing uses in-memory fakes only. Responses can
contain unrelated secrets: this tool displays only the signup boolean and schema
names. It disables redirects, uses certificate verification, bounded responses and
20-second request timeouts, and never saves the token. Do not run under shell tracing.

Verified against official Management API documentation on 2026-11-26:

- GET/PATCH `/v1/projects/{ref}/config/auth`: `disable_signup` (boolean).
  https://supabase.com/docs/reference/api/v1-get-auth-service-config
  https://supabase.com/docs/reference/api/v1-update-auth-service-config
- GET/PATCH `/v1/projects/{ref}/postgrest`: `db_schema` (comma-separated string).
  https://supabase.com/docs/reference/api/v1-get-postgrest-service-config
  https://supabase.com/docs/reference/api/v1-update-postgrest-service-config

Token permissions documented by these endpoints: `auth_config_read`,
`auth_config_write`, `project_admin_write`, `data_api_config_read`,
`data_api_config_write`. Scope to the intended project only; do not request
`data_api_config_secret_read`. Exact dashboard scope-picker labels may differ.

STEP 1: Review current configuration
RUN ON: [VS Code terminal on my Mac]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`
YOU WILL BE PROMPTED FOR:
- Project ref, hidden: Supabase project → Project Settings → General → Reference ID.
- Short-lived scoped personal access token, hidden: account menu → Account Settings
  → Access Tokens. Confirm the current labels if the dashboard differs. Store no
  token in shell history, files, arguments or chat.
COMMAND: `~/hmt-venv-3.13.10/bin/python tools/ops_supabase_harden.py`
EXPECTED OUTPUT: current signup boolean, exposed schema names, `DRY RUN: GET only`.
IF IT FAILS: send only the fixed STOP line, never HTTP bodies or token values.

STEP 2: Apply only the reviewed changes
RUN ON: [VS Code terminal on my Mac]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`
YOU WILL BE PROMPTED FOR: the same hidden inputs, then literal `APPLY` confirmation.
COMMAND: `~/hmt-venv-3.13.10/bin/python tools/ops_supabase_harden.py --apply`
EXPECTED OUTPUT: `PASS: sign-ups disabled and private schema excluded; verified by GET`.
IF IT FAILS: send only the fixed STOP line. The two PATCH requests are not atomic;
inspect the dashboard after a partial failure and rerun the read-only review.
An empty or unusually quoted schema list requires manual dashboard review.
The pre-write reread reduces races but cannot replace API-side compare-and-swap;
make no concurrent dashboard configuration edits during this procedure.
Rollback requires an explicit reviewed dashboard change; this script never re-enables
signups or exposes the private schema.

STEP 3: Revoke the temporary token
RUN ON: [Supabase dashboard]
FOLDER: not applicable (dashboard)
YOU WILL BE PROMPTED FOR: dashboard authentication only, in your browser.
COMMAND: account menu → Account Settings → Access Tokens → the temporary token → Revoke.
EXPECTED OUTPUT: token revoked. Menu wording may vary; do not revoke unrelated tokens.
IF IT FAILS: report the menu label or a redacted status only. Never share the token.
