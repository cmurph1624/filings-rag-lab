# The serving hostname gets its own zone. The parent domain stays at its current DNS host,
# which delegates this name with NS records (outputs.name_servers).

resource "aws_route53_zone" "app" {
  name    = var.domain
  comment = "Filings RAG Lab serving app; delegated from the parent domain's DNS host"
}
