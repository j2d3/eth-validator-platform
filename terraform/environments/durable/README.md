# Durable secret state

This root owns only the identity-addressed AWS Secrets Manager containers that
must survive an EKS/VPC/RDS cold-standby teardown. It uses a separate S3 state
key (`environments/durable/terraform.tfstate`) and every secret resource has
`prevent_destroy = true`.

The first migration from the former development root is explicit:

1. Initialize this root against the existing encrypted S3 backend.
2. Import the already-created secret containers by name; do not create new
   containers or write secret values.
3. Apply this root and verify imported names/ARNs without reading values.
4. Change the development root to its data-source form and verify its plan has
   no secret resource ownership.
5. Remove old secret resources from the development state only after the
   durable root is applied and state backups are recorded.

The cold-standby teardown must never use a broad destroy against this root.
