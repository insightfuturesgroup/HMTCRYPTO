# Public-repository identifier guard

`tools/check_public_identifiers.py` scans current Git-tracked files for five
environment identifiers. It stores no identifier plaintext. The manifest
`tools/public_identifier_fingerprints.json` contains a kind, a random 32-byte
salt and a SHA-256 digest for each identifier. The digest input is the decoded
salt bytes, one NUL separator and the identifier's exact ASCII bytes.

The scanner extracts project-reference, instance-identifier and IPv4-shaped
candidates, including embedded occurrences. Matching output contains only a file
path, line number and category. Content is never printed. Tracked operator env
files and certificate/key files are rejected before reading them. Symlinks are
scanned as links without following their targets. There is no file allowlist.

GitHub verification runs this check before building the test image or running
lint. It exits nonzero for matches, missing Git/manifest data or unsafe tracked
files. An image without Git checkout metadata cannot substitute for this check.

Run from the repository root:

```bash
python tools/check_public_identifiers.py
```

## Regenerate the manifest

Use an interactive terminal in the reviewed repository:

```bash
python tools/check_public_identifiers.py --regenerate
```

The tool explains the change and requires `REPLACE HASHES` confirmation before
prompting. Supply, through hidden prompts, the instance identifier, both Elastic
IPs and both project references. Obtain these from the operator's AWS and
Supabase dashboards. Do not put them in command arguments, shell history, chat
or a repository file. The tool saves only new random salts and hashes after all
five prompts pass validation. Review that the diff contains only salts/digests,
then run the check again. Regeneration does not fetch, push or rewrite history.

This guard checks current tracked content. Previously published identifiers
remain in Git history under the owner's explicit acceptance; history is not
rewritten. It complements the required pre-push diff scan.

## Migration helper input

Templates use `<project-ref>`. The migration helper has no project default.
It asks through a hidden prompt unless `HMT_SUPABASE_PROJECT_REF` already exists
in the operator's private process environment. Prefer the hidden prompt; do not
set the actual value inline in a shell command. Existing owner/runtime role,
pooler-suffix, port and query-parameter guards are unchanged. Password prompts
remain hidden and password URL encoding is unchanged.
