# Publish the audit branch for review

No deployment or gate change is part of these steps. The verification script is
read-only, so it does not prompt for mutation approval. No credentials are collected
or read. Later local operator configuration belongs in ignored `ops/local.env`,
created with mode 0600 by the applicable script; do not create it for this step.

STEP 1: Open the persistent audit worktree
RUN ON: [VS Code terminal on my Mac]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`
YOU WILL BE PROMPTED FOR: Nothing.
COMMAND: `code .` (or VS Code File > Open Folder and select this directory).
EXPECTED OUTPUT: VS Code opens the worktree; the status bar shows `codex/audit-fixes`.
IF IT FAILS: Send the command error and branch name only; no environment or credential output.

STEP 2: Publish or push using Source Control
RUN ON: [VS Code terminal on my Mac]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`
YOU WILL BE PROMPTED FOR: VS Code may request GitHub sign-in. Complete it in VS Code/browser;
never paste a token or password into chat. A remote chooser, if shown, should use `origin`.
COMMAND: In the same VS Code window, open Source Control. If shown, click Publish Branch
and choose origin. If already published, use Source Control > ... > Push. Inspect
the branch first; do not publish main, synchronize/merge, or force-push.
EXPECTED OUTPUT: The push completes for `codex/audit-fixes`.
IF IT FAILS: Send only the error category, such as publickey or permission denied;
omit URLs containing credentials, tokens and authentication dialogs.

STEP 3: Verify the local push state
RUN ON: [VS Code terminal on my Mac]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes`
YOU WILL BE PROMPTED FOR: Nothing. No network or changes are performed.
COMMAND: `bash ops/mac/00_verify_push.sh`
EXPECTED OUTPUT: `git status -sb` shows the audit branch and no ahead count;
`git log origin/codex/audit-fixes..HEAD` has no commits. The script prints PASS.
This uses the local tracking ref; separately compare GitHub's branch commit.
IF IT FAILS: Send the branch/status line and abbreviated pending commit IDs only.

STEP 4: Open a draft pull request
RUN ON: [GitHub]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes` (local reference; this step uses the browser).
YOU WILL BE PROMPTED FOR: No script inputs. In HMTCRYPTO > Pull requests > New pull request,
select base main and compare codex/audit-fixes. If the menu wording differs, use
the repository's branch-comparison page and its draft-PR creation action.
COMMAND: Enter title `Audit fixes (work in progress, do not merge)` and body
`Offline-reviewed audit fixes; gates remain closed. Work in progress—do not merge.`
Use the create-button dropdown to select Create draft pull request.
EXPECTED OUTPUT: A Draft PR from codex/audit-fixes to main; no merge or deployment.
IF IT FAILS: Send the safe UI error or PR number, without authentication information.

STEP 5: Inspect verification results
RUN ON: [GitHub]
FOLDER: `cd ~/HMT_Hitashi/HMTCRYPTO-fixes` (local reference; this step uses the browser).
YOU WILL BE PROMPTED FOR: Nothing. Open HMTCRYPTO > Actions > verify > the latest
audit-branch/PR commit. Also inspect the PR Checks tab.
COMMAND: Review backend, ui, postgres-acceptance and deployment-shape jobs and
download the full-suite-pg17 artifact when available. Do not run a deploy workflow.
EXPECTED OUTPUT: Passing verification jobs with exactly two documented scoped-FK
assertions reported as XFAIL by tools.ci_pytest_policy, not two unexplained red jobs:

- tests/integration/test_execution_schema_metadata.py::test_execution_metadata_tables_and_compiles_postgres
- tests/integration/test_identity_catalogue_schema.py::test_postgres_ddl_has_private_scoped_named_restrict_constraints

Any other failure, missing expected failure, unexpected pass, or safety skip/deselection
must fail verification. The expected-failure list must be empty before go-live.
A deduplicated push run may skip jobs when a PR run is selected; inspect that PR run.
IF IT FAILS: Send commit SHA, job names, failed test IDs, counts and skip reasons.
Do not paste complete logs until you have checked that they contain no secrets.
