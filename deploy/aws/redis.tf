# enable_redis = true: a shared answer cache and exact rate limits for all copies (REDIS_URL).
resource "aws_elasticache_subnet_group" "main" {
  count      = var.enable_redis ? 1 : 0
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_cluster" "main" {
  count                = var.enable_redis ? 1 : 0
  cluster_id           = var.name
  engine               = "redis"
  node_type            = var.redis_node_type
  num_cache_nodes      = 1
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.main[0].name
  security_group_ids   = [aws_security_group.redis[0].id]
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
}
