output "dns_records" {
  description = "Records for the domain's zone (infra/dns reads this with mail_state_path)."
  value = concat(
    [for token in aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens : {
      name   = "${token}._domainkey.${var.domain}"
      type   = "CNAME"
      values = ["${token}.dkim.amazonses.com"]
      ttl    = 1800
    }],
    [
      { name = local.mail_from, type = "MX", values = ["10 feedback-smtp.${var.region}.amazonses.com"], ttl = 300 },
      { name = local.mail_from, type = "TXT", values = ["v=spf1 include:amazonses.com ~all"], ttl = 300 },
      { name = "_dmarc.${var.domain}", type = "TXT", values = ["v=DMARC1; p=${var.dmarc_policy}"], ttl = 300 },
    ],
  )
}

output "smtp_host" {
  description = "SMTP endpoint for WordPress (port 587, STARTTLS)."
  value       = "email-smtp.${var.region}.amazonaws.com"
}

output "sender_user" {
  description = "IAM user for bin/mail-credentials (MAIL_SENDER_USER)."
  value       = aws_iam_user.sender.name
}

output "identity_arn" {
  value = aws_sesv2_email_identity.domain.arn
}

output "sandbox_note" {
  description = "A new SES account can only send to verified addresses until production access is granted."
  value       = "Check: aws sesv2 get-account --region ${var.region} (ProductionAccessEnabled). Request it with aws sesv2 put-account-details."
}
