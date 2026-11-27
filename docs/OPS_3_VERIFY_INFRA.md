# Ops-3: read-only Mumbai infrastructure checks

No AWS resources are changed. No credential prompts: use your existing CloudShell
session. The script prints fixed PASS/FAIL labels, not AWS accounts, instance IDs,
IPs, role ARNs, policy IDs or raw CLI errors. No operator configuration is saved.

STEP 1: Open the checkout in Mumbai CloudShell
RUN ON: [AWS CloudShell, region ap-south-1]
FOLDER: `cd ~/HMTCRYPTO`
YOU WILL BE PROMPTED FOR: nothing. Select Mumbai in the AWS console region picker.
COMMAND: `git status -sb`
EXPECTED OUTPUT: existing checkout on `codex/audit-fixes`. If absent, clone the public
repository into `~/HMTCRYPTO` first; do not use stored credentials or tokens.
IF IT FAILS: report only that the checkout is missing; do not send environment dumps.

STEP 2: Verify infrastructure
RUN ON: [AWS CloudShell, region ap-south-1]
FOLDER: `cd ~/HMTCRYPTO`
YOU WILL BE PROMPTED FOR: instance ID, hidden; EC2 → Instances → intended Mumbai
instance → Instance ID. Do not paste it into chat.
COMMAND: `bash ops/aws/10_verify_infra.sh`
EXPECTED OUTPUT: PASS/FAIL for state; t4g.medium/arm64; required IMDSv2 and hop limit 2;
only TCP 80/443 inbound (no SSH, UDP or other ports); termination protection; encrypted
gp3 volumes totalling at least 30 GiB; Elastic IP; SSM Online; enabled tagged DLM
snapshot policy covering every attached EBS volume.
IF IT FAILS: send fixed PASS/FAIL lines only. A CLI/permission failure means unverified,
not proof the infrastructure is unsafe. Do not share raw AWS errors or JSON.

Required read permissions: ec2:DescribeInstances, ec2:DescribeSecurityGroups,
ec2:DescribeInstanceAttribute, ec2:DescribeVolumes, ec2:DescribeAddresses,
ssm:DescribeInstanceInformation, dlm:GetLifecyclePolicies, dlm:GetLifecyclePolicy.
The checker makes no IAM or infrastructure changes.

Limitations: DLM tag/schedule coverage is configuration evidence only; it does not
prove successful snapshots or a tested restore. Default-policy selection, exclusion
rules, archive behavior and actual snapshot execution require separate review.
Hop limit 2 permits container role access: the API metadata block remains required
before any exchange credential exists. This checker does not install that block.
No rollback is needed for this read-only operation.
