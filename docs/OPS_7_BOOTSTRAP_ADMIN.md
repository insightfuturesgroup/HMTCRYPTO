# Ops-7: sole admin and locked account bootstrap

RUN ON: EC2 interactive Session Manager shell; never SSM send-command.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: Supabase Auth user UUID, its confirmed email, and admin full name. Obtain
them from the single pre-created Auth user. All are hidden and delivered to the
container through stdin, never command arguments, files, shell history or SSM text.

COMMAND: `bash ops/aws/50_bootstrap_admin.sh`

EXPECTED OUTPUT: three PASS lines: sole admin bound; locked account created with
entry/exit disabled; tmpfs API account binding updated and API recreated. The
script uses `platform bootstrap-admin` behavior and `account bootstrap --profile
delta-india-primary --confirm-locked-live` behavior without exposing inputs.

FAILURE FORMAT: share only the fixed STOP/PASS lines. Do not share UUIDs, email,
name, container output, database diagnostics or environment files.
