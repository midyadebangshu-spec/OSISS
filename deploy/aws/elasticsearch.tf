# elasticsearch_mode = "self_managed": one Elasticsearch 8 node on EC2 with its data on a separate EBS volume (kept
# if the instance is replaced). Reachable only from the API instances. Single node: no replica shards, so take EBS
# snapshots (AWS Backup / Data Lifecycle Manager) or use Elastic Cloud for high availability.

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_iam_role" "es" {
  count              = var.elasticsearch_mode == "self_managed" ? 1 : 0
  name               = "${var.name}-es"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "es_ssm" {
  count      = var.elasticsearch_mode == "self_managed" ? 1 : 0
  role       = aws_iam_role.es[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "es" {
  count = var.elasticsearch_mode == "self_managed" ? 1 : 0
  name  = "${var.name}-es"
  role  = aws_iam_role.es[0].name
}

resource "aws_instance" "es" {
  count                  = var.elasticsearch_mode == "self_managed" ? 1 : 0
  ami                    = nonsensitive(data.aws_ssm_parameter.al2023.value)
  instance_type          = var.es_instance_type
  subnet_id              = aws_subnet.private[0].id
  vpc_security_group_ids = [aws_security_group.es[0].id]
  iam_instance_profile   = aws_iam_instance_profile.es[0].name
  user_data = templatefile("${path.module}/elasticsearch-user-data.sh.tftpl", {
    heap = var.es_heap
  })

  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 30
    encrypted   = true
  }
  tags = { Name = "${var.name}-elasticsearch" }

  lifecycle {
    # A newer AMI must not replace the search index's host by surprise.
    ignore_changes = [ami, user_data]
  }
}

resource "aws_ebs_volume" "es" {
  count             = var.elasticsearch_mode == "self_managed" ? 1 : 0
  availability_zone = local.azs[0]
  size              = var.es_volume_gb
  type              = "gp3"
  encrypted         = true
  tags              = { Name = "${var.name}-elasticsearch-data" }
}

resource "aws_volume_attachment" "es" {
  count       = var.elasticsearch_mode == "self_managed" ? 1 : 0
  device_name = "/dev/sdf"
  volume_id   = aws_ebs_volume.es[0].id
  instance_id = aws_instance.es[0].id
}
