# Ops-2: Mumbai owner migration

RUN ON: Mac VS Code terminal.

FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`

PROMPTS: reviewed CI-green full SHA; `APP STOPPED`; hidden project reference;
absolute CA path; session-pooler hostname; hidden owner and runtime passwords.
Post-verification repeats the hidden project reference and owner password. Values
come from GitHub, Supabase Dashboard, the password manager, and
`~/hmt-secrets/supabase-ca.crt`. Ops-1 must be complete first.

COMMAND: `bash ops/mac/02_migrate_mumbai.sh`

EXPECTED OUTPUT: head `phase34_exact_decimal_v1`; 189 restrictive FKs; zero
PUBLIC/anon/authenticated EXECUTE grants on all six phase33 functions; and EXECUTE
on `purge_expired_audit_payload(uuid,text)` for `hmt_audit_retention`. Then run
`tools/ops_supabase_harden.py` without `--apply`; exposed schemas must remain
`public, graphql_public`, with `hmt_crypto` absent.

FAILURE FORMAT: share only the fixed failure category, STOP line, and PASS labels.
Never share URLs, passwords, tokens, database output, or tracebacks.
