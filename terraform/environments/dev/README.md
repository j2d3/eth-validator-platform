# Development environment

This root declares the single production-shaped but cost-aware testnet **Amazon
EKS** environment. It contains no Google/GKE adapter. Workers are private; the
API endpoint is private plus public access restricted to CIDRs supplied by a
trusted operator. The system group is separated from a tainted, on-demand
Ethereum group.

No AWS resource has been created or qualified from this repository. A clean
Terraform validation proves configuration shape, not an EKS runtime.

## Operating boundary

Version 1 is planned and applied from a trusted local workstation using the
operator's existing AWS authentication. There is intentionally no GitHub
Actions Terraform apply/destroy workflow and no AWS credential or OIDC trust in
the application workflows. GitHub Actions validates Terraform and creates
reviewed application/catalog changes; after cluster bootstrap, Flux is the
continuous writer for in-cluster applications.

Terraform owns the AWS foundation only. It does not continuously manage Helm
releases, dashboards, validator assignments, or node-pair lifecycle state.

## Declared versus missing

| Area | Declared in this root | Still required before Phase 4 exit |
|---|---|---|
| Networking | Three-AZ VPC; public, private worker, and intra control-plane subnets; DNS; flow logs; one NAT gateway by lab default | Runtime routing/egress evidence, VPC endpoints/cost decision, production NAT/AZ posture |
| EKS | Restricted public plus private API; control-plane logs; access-entry input | Actual apply, access qualification, upgrade and API-throttling evidence |
| Capacity | Two-node on-demand system group; tainted on-demand Ethereum group | Measured client sizing, EBS data-volume topology, autoscaling/Karpenter decision |
| Add-ons and identity | VPC CNI, CoreDNS, kube-proxy, EKS Pod Identity agent, EBS CSI with a dedicated role | Application EBS StorageClass, AWS External Secrets `SecretStore`, Flux EKS overlay |
| Secrets | Empty Secrets Manager container for the EL/CL Engine JWT; read-only External Secrets role scoped to it | Restricted operator value bootstrap, signing-key containers/policies, rotation evidence |
| Slashing database | Not declared | RDS PostgreSQL, subnet/security groups, credentials adapter, backups/PITR, restore and failover qualification |
| Encryption | AWS-managed encryption is requested for node root volumes and Terraform state | Explicit KMS/AWS-managed-key decision for RDS, EBS application data, and Secrets Manager |
| Public Ethereum networking | Not declared | P2P Service/NLB design, source ranges, discovery/TCP/UDP qualification |

Local CloudNativePG, local-path volumes, and the Kubernetes External Secrets
provider are contract-compatible development adapters; they are not evidence
for RDS, EBS, IAM, KMS, or EKS behavior.

## Trusted local plan/apply

Set `cluster_public_access_cidrs` to the operator workstation's trusted `/32`
(or use private connectivity). Keep the checked-in loopback default until that
decision is explicit; it makes an accidental public-API apply inaccessible
rather than broadly exposed.

```bash
cp terraform/environments/dev/backend.hcl.example terraform/environments/dev/backend.hcl
cp terraform/environments/dev/terraform.tfvars.example terraform/environments/dev/terraform.tfvars
terraform -chdir=terraform/environments/dev init -backend-config=backend.hcl
terraform -chdir=terraform/environments/dev plan
```

Review and save the plan before the eventual manual apply. Do not add an
automatic apply merely to avoid this operator checkpoint. The root is not yet a
Phase 4-complete environment; the table above is the remaining work list.

The lab defaults to one NAT gateway for cost. A production-shaped environment sets `single_nat_gateway = false`, isolates additional signing/data tiers, and uses a private runner or private control-plane connectivity.

Terraform creates the Secrets Manager object for the Engine API JWT but not its
value. A separate restricted operator bootstrap must generate and write that
value directly to Secrets Manager without placing it in Terraform state, Git,
shell history, or workflow logs. That bootstrap is not implemented yet, so the
secret container is declared infrastructure rather than runtime readiness.
