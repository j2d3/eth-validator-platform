# Runtime qualification evidence

This directory holds reviewed, public-safe records of behavior observed on live
infrastructure. A desired-state manifest, passing offline test, chat transcript,
or uncommitted terminal scrollback is not runtime evidence.

Each record must name the tested repository commit and UTC time, state the
positive and negative observations, and distinguish pass/fail assertions from
operator interpretation. It must not contain cloud account IDs, ARNs,
security-group or network-interface IDs, public or private IP addresses, secret
values, raw environment dumps, kubeconfigs, or credentials.

## Records

- [EKS NetworkPolicy enforcement](2026-08-04-eks-network-policy.md)
- [First Web3Signer-backed validator duty](2026-08-04-first-signing-validator.md)

The first signing record covers the positive validator-client → Web3Signer →
RDS → beacon-chain path. RDS recovery, conflicting-duty rejection, and
signer-specific negative network probes remain separate evidence requirements.
