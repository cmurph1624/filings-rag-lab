# The instance's role: SSM agent (no SSH), pull images, read its secrets, read index dumps and
# deploy bundles, and write backups without being able to read or delete them.

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${local.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "instance_ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn]
  }
  statement {
    # SecureStrings use the AWS-managed aws/ssm key, whose policy allows decryption through
    # SSM for principals with these permissions.
    sid       = "Secrets"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/frl/prod/*"]
  }
  statement {
    sid       = "ReadDumpsAndBundles"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/serving/*", "${aws_s3_bucket.data.arn}/deploy/*"]
  }
  statement {
    sid       = "ListDumpsAndBundles"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["serving/*", "deploy/*"]
    }
  }
  statement {
    sid       = "WriteBackups" # put-only: a compromised host can't read or delete old backups
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/backups/*"]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${local.name}-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
}
