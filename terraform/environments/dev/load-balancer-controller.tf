# This policy is semantically vendored from the controller's v3.5.0 release:
# https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v3.5.0/docs/install/iam_policy.json
# Pinning the policy beside the exact chart/image release keeps Terraform plans
# independent of network-time downloads and makes permission changes reviewable.
resource "aws_iam_policy" "load_balancer_controller" {
  name        = "${local.name}-aws-load-balancer-controller"
  description = "AWS Load Balancer Controller v3.5.0 permissions for ${module.eks.cluster_name}."
  policy      = file("${path.module}/iam/aws-load-balancer-controller-v3.5.0.json")

  tags = local.tags
}

resource "aws_iam_role" "load_balancer_controller" {
  name               = "${local.name}-aws-load-balancer-controller"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json

  tags = local.tags
}

resource "aws_iam_role_policy_attachment" "load_balancer_controller" {
  role       = aws_iam_role.load_balancer_controller.name
  policy_arn = aws_iam_policy.load_balancer_controller.arn
}

resource "aws_eks_pod_identity_association" "load_balancer_controller" {
  cluster_name    = module.eks.cluster_name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.load_balancer_controller.arn
}
