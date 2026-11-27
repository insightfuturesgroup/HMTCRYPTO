# Ops-5: interactive protected EC2 configuration

RUN ON: EC2 interactive Session Manager shell; never SSM send-command.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: CA base64 and SHA256 from Mac; runtime and owner database URLs; Argon2
operator hash; Supabase URL, publishable key and server key; TLS email. Every value
is entered through a hidden prompt and never appears in shell history or SSM text.

COMMAND: `bash ops/aws/30_install_config.sh`

EXPECTED OUTPUT: PASS lines confirm `/etc/hmt` is root mode 0700; the CA and four
environment files are root mode 0600; checkout links target `/etc/hmt`; legacy
`/run/hmt` links are removed; and every production/dispatch gate remains closed.
Files are written atomically with a restrictive umask and persist across reboot.

FAILURE FORMAT: share only fixed FAIL/PASS lines. Never share URLs, keys, hashes,
CA base64, environment files or traces. On Mac, calculate the digest with
`shasum -a 256 ~/hmt-secrets/supabase-ca.crt`; paste base64 directly from
`base64 < ~/hmt-secrets/supabase-ca.crt | tr -d '\n'` into the hidden prompt.
