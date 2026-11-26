# Ops-1: create the restricted runtime role

Operator-only script. Never run it through an agent, shared terminal recording,
shell tracing, CI, or a terminal with session logging enabled. All credentials
stay in process memory. No URL/password is passed as a command argument, written
to a file, or forwarded in server diagnostics. `psql` performs password encryption
using its own `\password` command through a private terminal with echo disabled.

STEP 1: Review and preview

RUN ON: [VS Code terminal on my Mac]

FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`

YOU WILL BE PROMPTED FOR: nothing in dry-run mode.

COMMAND:

```bash
bash ops/mac/01_create_runtime_role.sh --dry-run
```

EXPECTED OUTPUT: `DRY RUN: no prompts, connections, file reads, or changes performed.`
If psql is missing, the script prints `brew install libpq` and the bash/zsh PATH
command. Install it yourself, then rerun the preview. The helper uses Python 3's
standard library; no application installation is required.

IF IT FAILS: send only the script's fixed `STOP` line. Do not send environment
variables, process dumps, credentials, or screenshots containing hidden inputs.

STEP 2: Apply the reviewed role plan

RUN ON: [VS Code terminal on my Mac]

FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`

YOU WILL BE PROMPTED FOR:

- Session pooler hostname, hidden: Supabase project → **Connect** → **Session
  pooler**; copy the hostname only. Port is fixed at 5432, not transaction pooling.
- Project reference, hidden: the same Connect dialog's qualified username suffix
  or Project Settings → General. An already-private `HMT_SUPABASE_PROJECT_REF`
  variable is also supported; never set its value inline in shell history.
- CA path, hidden: Enter defaults to `~/hmt-secrets/supabase-ca.crt`. Download the
  project database CA yourself from the dashboard's database connection/SSL
  settings. The precise CA-download menu label may vary; verify it in your project.
- Owner postgres password, hidden: your password manager, after your own dashboard
  password reset. This is the database password, not a personal access token.
- For an existing role only: type `RESET` to change its password, or Enter to keep it.
- New runtime password and confirmation, hidden: generate/store it yourself in
  your password manager. Minimum 20 characters, maximum 1024; no newline or NUL.
  Punctuation is supported without URL percent-encoding because no URL is built.
- Type `APPLY` to authorize role creation/verification and CONNECT grant.

COMMAND:

```bash
bash ops/mac/01_create_runtime_role.sh
```

EXPECTED OUTPUT: `PASS: restricted runtime role and CONNECT verified; requested password step completed.`

The role is LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION,
NOBYPASSRLS. Only CONNECT is granted here; Alembic later grants reviewed object
privileges. Existing roles with unsafe attributes are rejected, not silently
altered. A rerun keeps an existing password unless you explicitly request RESET.
Connections use certificate-verified TLS and the project-qualified owner username.

IF IT FAILS: send only the fixed `STOP` lines. Check host/project, CA, connectivity
and owner permissions locally. If password setup failed after role creation,
rerun and choose RESET: a new role without a password cannot log in by password.
Role creation and password assignment are separate steps and cannot be rolled
back as one transaction. Do not drop an existing role as a troubleshooting step.

This does not authorize migrations. Ops-2 remains blocked until PostgreSQL
acceptance is green and the Supabase realism check passes. No gate is opened.
