resource "aws_ecr_repository" "portal" {
  name                 = "${local.name}/portal"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  # Repository-level basic scanning is deliberately scoped to this project.
  # Do not replace it with the Region/account singleton in this shared account.
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = merge(local.tags, {
    Component = "portal"
  })
}

resource "aws_ecr_lifecycle_policy" "portal" {
  repository = aws_ecr_repository.portal.name

  # The portal is reproducible application content, not validator chain data.
  # Keep a rollback window while bounding all tagged and untagged storage.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged portal images after 14 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 14
        }
        action = {
          type = "expire"
        }
      },
      {
        # AWS requires a tagStatus=any rule to have the lowest evaluation
        # priority (the largest rulePriority number).
        rulePriority = 2
        description  = "Expire portal images older than the newest 30"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 30
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}
