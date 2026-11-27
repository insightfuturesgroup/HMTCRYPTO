# Ops-5: interactive protected EC2 configuration

RUN ON: EC2 interactive Session Manager shell; never SSM send-command.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: CA base64 and SHA256 from Mac; runtime and owner database URLs; Argon2
operator hash; Supabase URL, publishable key and server key; TLS email. Every value
is entered through a hidden prompt and never appears in shell history or SSM text.

COMMAND: `bash ops/aws/30_install_config.sh`

EXPECTED OUTPUT: three PASS lines for CA verification, tmpfs configuration, and
closed gates. The public CA is mode 600 at `/etc/hmt/supabase-prod-ca.crt`.
Secret env files are root mode 600 in `/run/hmt` (tmpfs), not on disk; rerun ops-5
after every reboot. Checkout paths are symlinks only.

FAILURE FORMAT: share only fixed STOP/PASS lines. Never share URLs, keys, hashes,
CA base64, environment files or traces. On Mac, calculate the digest with
`shasum -a 256 ~/hmt-secrets/supabase-ca.crt`; paste base64 directly from
`base64 < ~/hmt-secrets/supabase-ca.crt | tr -d '\n'` into the hidden prompt.
