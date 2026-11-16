# Optional stack: outgoing mail for WordPress through Amazon SES, in the site's account.
# Nothing here receives mail. The domain is verified with Easy DKIM and a custom MAIL FROM
# domain; the DNS records it needs are an output that infra/dns can publish
# (mail_state_path), in whatever account holds the zone.
#
# Lightsail instances have no IAM role, so WordPress authenticates with SMTP credentials
# derived from an access key of a dedicated IAM user. That user may only send raw mail
# through this identity, and only from the listed From addresses. Its access key is not
# created here (it would sit in the Terraform state): bin/mail-credentials creates it and
# writes the SMTP credentials straight to the server.

data "aws_caller_identity" "this" {}

locals {
  mail_from = "${var.mail_from_subdomain}.${var.domain}"
}

resource "aws_sesv2_email_identity" "domain" {
  email_identity = var.domain

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

resource "aws_sesv2_email_identity_mail_from_attributes" "domain" {
  email_identity         = aws_sesv2_email_identity.domain.email_identity
  mail_from_domain       = local.mail_from
  behavior_on_mx_failure = "USE_DEFAULT_VALUE"
}

resource "aws_iam_user" "sender" {
  name = var.sender_name
  path = "/wordpress/"
}

data "aws_iam_policy_document" "send" {
  statement {
    sid       = "SendAsTheSiteOnly"
    actions   = ["ses:SendRawEmail"]
    resources = [aws_sesv2_email_identity.domain.arn]

    condition {
      test     = "StringEquals"
      variable = "ses:FromAddress"
      values   = var.from_addresses
    }
  }
}

resource "aws_iam_user_policy" "send" {
  name   = "send-mail-as-${replace(var.domain, ".", "-")}"
  user   = aws_iam_user.sender.name
  policy = data.aws_iam_policy_document.send.json
}
