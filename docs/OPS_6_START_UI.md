# Ops-6: locked first UI start

RUN ON: EC2 interactive Session Manager shell.

FOLDER: `cd /opt/hmt-crypto`

PROMPTS: reviewed full SHA and hidden Elastic IP from the EC2 console. No credential
is a command argument. Ops-4b and ops-5 must already have passed.

COMMAND: `bash ops/aws/40_start_ui.sh`

EXPECTED OUTPUT: PASS for closed-gate release preflight, protected `/etc/hmt` env, DNS, TCP 80/443
readiness, memory, disk and images; then ordered starts for redis, API/worker/private
worker, UI and Caddy with `--no-deps --no-build`; API liveness status 200.

FAILURE FORMAT: share only STOP/PASS lines, service name and HTTP status. Inspect
logs locally for secrets before sharing any excerpt. No migrate service is run.

STOP COMMAND: `bash ops/aws/40_start_ui.sh --stop`. It preserves images, protected
configuration and Docker volumes. It does not alter AWS resources or gates.
