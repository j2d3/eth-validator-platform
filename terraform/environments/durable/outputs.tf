output "secret_arns" {
  description = "Durable Secrets Manager container ARNs; values are never exposed."
  value = {
    engine   = aws_secretsmanager_secret.engine_jwt.arn
    database = aws_secretsmanager_secret.web3signer_database.arn
    signing = {
      for validator_id, secret in aws_secretsmanager_secret.web3signer_signing_key :
      validator_id => secret.arn
    }
  }
}
