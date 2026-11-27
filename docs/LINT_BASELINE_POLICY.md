# Reviewed lint baseline

At `45753452620`, Ruff 0.9.10 reported 76 findings: one introduced F401,
59 pre-existing UP038, and 16 pre-existing UP040. No noqa directives are added.

- F401: remove only the unused BASELINE import in
  `tests/integration/test_reconciliation_run_schema.py`. No assertion changes.
- UP038: convert **18** sites across 11 files only where every tuple member is an
  unshadowed built-in type. AST comparison verifies that these edits change only
  the isinstance tuple syntax. Seven distinct type combinations are tested on
  18 representative values. No dispatch, claim, recovery or risk behavior is
  changed. The affected market, limiter, redaction and security tests are rerun.
- The remaining **41 UP038** sites retain their tuple syntax under a written
  rule exclusion. They involve imported/third-party classes, ABCs, exception/AST
  types, or custom application types. Union construction can invoke a metaclass's
  overloaded `__or__`, while an isinstance tuple does not. A regression test
  demonstrates that distinction. Do not automatically convert those sites
  without qualifying their specific runtime types and consumers.
- All **16 UP040** sites are deferred under a separate written rule exclusion.
  Review found eight union-expression aliases and eight subscription-expression
  aliases. Eager assignments produce actual runtime typing objects; PEP 695
  aliases produce TypeAliasType instances with lazy evaluation. That changes
  failure timing and runtime identity/introspection. The new test proves lazy
  name resolution and TypeAliasType identity; no alias is rewritten.

These are explicit review decisions, not claims that all baseline debt is fixed.
Only UP038 and UP040 are ignored; E/F/I/UP/B/ASYNC remain selected, including
F401 and undefined-name checks. The policy regression enforces that exact set.

The test image now includes `.dockerignore`, so its existing file-boundary test
can execute instead of failing with FileNotFoundError. No operator env/CA files
are copied. The backend workflow now explicitly runs the offline tooling and
migration-safety pytest subset after Ruff and before mypy, using `--network none`.
The full suite remains in the PG17 job so database safety tests receive their
disposable service. These workflow changes are verification only, not deployment.

Package installation/image preparation can use registries; local verification
uses Python 3.13.10 and denies external networking. Local targeted results and
the queued post-push CI status must be reported separately. Revert this lint
commit to restore the prior rule policy and syntax; no database downgrade or
gate change is involved.
