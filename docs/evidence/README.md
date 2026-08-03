# Runtime qualification evidence

This directory holds reviewed, public-safe records of behavior observed on live
infrastructure. A desired-state manifest, passing offline test, chat transcript,
or uncommitted terminal scrollback is not runtime evidence.

Each record must name the tested repository commit and UTC time, state the
positive and negative observations, and distinguish pass/fail assertions from
operator interpretation. It must not contain cloud account IDs, ARNs,
security-group or network-interface IDs, public or private IP addresses, secret
values, raw environment dumps, kubeconfigs, or credentials.

The first EKS signer-admission evidence is intentionally absent. The Flux EKS
bootstrap runbook requires a reviewed NetworkPolicy allow/deny record here
before either signer layer is unsuspended, followed by sanitized branch-ENI
attachment assertions for the migration and Web3Signer Pods.
