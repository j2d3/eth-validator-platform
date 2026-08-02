# Development environment

This root creates a production-shaped but cost-aware testnet EKS environment. Workers are private; the API endpoint is public only for explicit CIDRs so a GitHub-hosted runner can bootstrap Flux. The system group is separated from a tainted, on-demand Ethereum group.

```bash
cp terraform/environments/dev/backend.hcl.example terraform/environments/dev/backend.hcl
cp terraform/environments/dev/terraform.tfvars.example terraform/environments/dev/terraform.tfvars
terraform -chdir=terraform/environments/dev init -backend-config=backend.hcl
terraform -chdir=terraform/environments/dev plan
```

The lab defaults to one NAT gateway for cost. A production-shaped environment sets `single_nat_gateway = false`, isolates additional signing/data tiers, and uses a private runner or private control-plane connectivity.

Terraform creates the Secrets Manager object for the Engine API JWT but not its value. The apply workflow generates and writes the value directly with the AWS API, keeping secret material out of Terraform state.
