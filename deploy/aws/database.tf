resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "main" {
  identifier                 = var.name
  engine                     = "postgres"
  engine_version             = "15"
  instance_class             = var.db_instance_class
  allocated_storage          = var.db_allocated_storage_gb
  max_allocated_storage      = var.db_allocated_storage_gb * 4
  storage_type               = "gp3"
  storage_encrypted          = true
  db_name                    = "osiss"
  username                   = "osiss"
  password                   = random_password.db.result
  db_subnet_group_name       = aws_db_subnet_group.main.name
  vpc_security_group_ids     = [aws_security_group.db.id]
  multi_az                   = var.db_multi_az
  backup_retention_period    = 7
  deletion_protection        = var.deletion_protection
  skip_final_snapshot        = !var.deletion_protection
  final_snapshot_identifier  = var.deletion_protection ? "${var.name}-final" : null
  auto_minor_version_upgrade = true
  apply_immediately          = false
  publicly_accessible        = false
}
