# waf_search_limit_per_5min > 0: AWS WAF blocks an address that sends more /api/search requests than that in any
# 5-minute window, before they reach the copies (complements the in-app per-IP limit).
resource "aws_wafv2_web_acl" "main" {
  count = var.waf_search_limit_per_5min > 0 ? 1 : 0
  name  = var.name
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "search-rate-limit"
    priority = 1
    action {
      block {}
    }
    statement {
      rate_based_statement {
        limit              = var.waf_search_limit_per_5min
        aggregate_key_type = "IP"
        scope_down_statement {
          byte_match_statement {
            search_string         = "/api/search"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-search-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "main" {
  count        = var.waf_search_limit_per_5min > 0 ? 1 : 0
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main[0].arn
}
