# EKS NetworkPolicy allow/deny qualification — 2026-08-04

## Observation identity

- UTC observation time: `2026-08-04T02:03:51Z`
- tested repository commit: `5479aade8445c7718484f7850763fe739b3e0238`
- cluster profile: EKS development lab
- Amazon VPC CNI add-on: `v1.22.4-eksbuild.3`

The test used the committed
`hack/qualification/eks-network-policy-probe.yaml` fixture without edits. It
created one server Deployment and two client Deployments in a disposable
`network-policy-probe` namespace. Neither client had a
`SecurityGroupPolicy`; the destination, Service, TCP port, and client image
were identical. Only one client carried the label admitted by the destination
NetworkPolicy.

## Preconditions

The four Terraform-managed VPC CNI settings observed through the EKS add-on
configuration were:

| Setting | Observed value |
|---|---|
| `enableNetworkPolicy` | `true` |
| `ENABLE_POD_ENI` | `true` |
| `NETWORK_POLICY_ENFORCING_MODE` | `standard` |
| `POD_SECURITY_GROUP_ENFORCING_MODE` | `standard` |

The server, allowed-client, and denied-client Deployments each completed their
rollout before the network requests ran.

## Assertions

| Assertion | Observation | Result |
|---|---|---|
| Labeled client reaches the policy-selected server path | HTTP response body was exactly `network-policy-ok` | Pass |
| Otherwise identical unlabeled client is denied on the same Service and TCP port | `wget` returned non-zero status `1` | Pass |
| Qualification resources do not remain in the cluster | The namespace deletion completed and a subsequent namespace lookup failed | Pass |

## Interpretation and limits

The paired result demonstrates that the VPC CNI enforced the committed ingress
NetworkPolicy selector on this in-cluster TCP path. The negative result is not
attributable to an RDS security group because both clients targeted the same
Kubernetes Service and neither used a Pod security group.

This record does not qualify egress policy, cross-namespace policy, Pod branch
ENI attachment, RDS admission, public NLB behavior, or any validator/signing
path. The signer and application Flux layers remained suspended, and this
experiment created no Ethereum or signing workload.
