# Terraform state bootstrap

Run this root once with locally authenticated AWS credentials. It intentionally uses local state to create the versioned, encrypted, non-public S3 state bucket used by the environment roots. Terraform's S3-native lockfile is enabled; the deprecated DynamoDB locking mechanism is not provisioned.

```bash
cp terraform/bootstrap/terraform.tfvars.example terraform/bootstrap/terraform.tfvars
terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap apply
```

The bucket uses `prevent_destroy`. Removing it is a separate recovery/governance decision, not part of normal cluster teardown.
