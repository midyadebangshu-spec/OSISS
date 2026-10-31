# OSISS on AWS (reference deployment)

Terraform for running OSISS for everyone at your own domain, with several search copies that scale with demand.
It is a starting point to adapt, not a managed service: read what it creates and what it costs before applying it.

```
                         Internet
                            │ HTTPS (ACM certificate)            optional AWS WAF: per-IP rate limit on /api/search
                   ┌────────▼────────┐
                   │  Load balancer  │  health check /ready; /api/admin/* optionally only from admin_cidrs
                   └───┬─────────┬───┘
            ┌──────────▼──┐   ┌──▼──────────┐
            │ search copy │ … │ search copy │   GPU instances, auto scaling on requests per copy
            └──────┬──────┘   └──────┬──────┘   (INGEST_WORKER=false)
                   │                 │                       ┌──────────────┐
                   ├─────────────────┼───────────────────────│ ingestion    │  1 instance, replaced if it fails
                   │                 │                       │ worker       │  (INGEST_WORKER=true, creates the schema)
   ┌───────────────▼──┐  ┌───────────▼───────┐  ┌──────────┐ └──────────────┘
   │ RDS PostgreSQL   │  │ Elasticsearch     │  │ S3 bucket│  pdfs/  uploads/  models/
   │ books, settings, │  │ EC2 node (or      │  └──────────┘
   │ jobs, copies     │  │ Elastic Cloud)    │  optional ElastiCache Redis (shared cache, exact rate limits)
   └──────────────────┘  └───────────────────┘  Secrets Manager (admin token, DB password) · CloudWatch logs
```

Everything except the load balancer is in private subnets. There are no SSH keys: open a shell with Session Manager
(`aws ssm start-session --target <instance id>`).

## What it costs

The main costs are hourly, whether or not anyone searches. Price them for your region with the
[AWS Pricing Calculator](https://calculator.aws/) before applying:

| Item | Default | Notes |
|---|---|---|
| Search copies | 2 × `g5.xlarge` (scales 1–4) | By far the largest cost. Lower `search_min` / `search_desired` outside term time. |
| Ingestion worker | 1 × `g4dn.xlarge` | `worker_use_gpu = false` with a CPU type is cheaper but embeds more slowly. |
| Elasticsearch | 1 × `r6i.large` + 100 GB gp3 | Or an Elastic Cloud subscription (`elasticsearch_mode = "elastic_cloud"`). |
| Database | `db.t4g.medium`, 50 GB gp3 | `db_multi_az = true` adds a standby (about double). |
| Load balancer, NAT gateway | 1 each | Hourly plus data processed. S3 traffic skips the NAT (gateway endpoint). |
| S3, ECR, Secrets Manager, CloudWatch logs | small | Grow with the library and log volume. |
| Optional | Redis `cache.t4g.small`, WAF | Only when enabled. |

GPU instances need a quota: check **Service Quotas → EC2 → Running On-Demand G and VT instances** (vCPUs). The
defaults use 3 × 4 = 12 vCPUs; new accounts often start at 0, and increases can take a day or more.

## Before you start

* An AWS account and credentials allowed to create the resources above (an administrator, for the first time).
* Terraform 1.6+, the AWS CLI v2, `jq` and Docker on your machine; the OSISS repository with the models downloaded
  (`./setup.sh` or `.venv/bin/python src/download_models.py`).
* A domain you control. With its DNS in Route 53, set `route53_zone_id` and the records are created for you.
* Somewhere private for the Terraform state (see the `backend` block in `versions.tf`): the state holds the
  generated admin token and database password.

## Step by step

Run everything from `deploy/aws` unless noted.

1. **Configure.** `cp terraform.tfvars.example terraform.tfvars` and set at least `region`, `domain_name` and, ideally,
   `admin_cidrs` (your office's public IP for the admin app). Then `terraform init`.

2. **Create the image registry and the bucket first:**

   ```bash
   terraform apply -target=aws_ecr_repository.api -target=aws_s3_bucket.data
   ```

3. **Build and push the API image** (from the repository root; on an Apple-silicon Mac add `--platform linux/amd64`):

   ```bash
   terraform -chdir=deploy/aws output -raw push_image_commands   # prints the three commands
   aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <registry>
   docker build -t <repository url>:latest .
   docker push <repository url>:latest
   ```

4. **Upload the models** (about 12 GB, once; new instances fetch them from here):

   ```bash
   S3_REGION=<region> .venv/bin/python src/sync_models.py --upload "$(terraform -chdir=deploy/aws output -raw models_s3_uri)"
   ```

5. **HTTPS certificate.** With `route53_zone_id` set, skip this step. Otherwise request the certificate and add its
   validation record at your DNS provider:

   ```bash
   terraform apply -target=aws_acm_certificate.site
   terraform output certificate_validation        # add this CNAME at your DNS provider
   ```

6. **Create everything else:** `terraform apply`. It waits until the certificate is issued, then creates the
   database, Elasticsearch, the instances and the load balancer. New copies download the image and the models and load
   the models before the load balancer sends them traffic; the first start takes roughly 15–30 minutes.

7. **Point the domain at the load balancer.** With Route 53 this is done. Otherwise create a CNAME from your domain to
   `terraform output -raw load_balancer_dns_name` (at the bare domain, e.g. `ossis.com`, use your DNS provider's
   ALIAS/ANAME record, or serve the site at `www.`).

8. **Check it:** `curl https://<domain>/ready` returns `{"ready": true, ...}` once a copy is ready. The search page is
   at `https://<domain>`.

9. **Connect the admin app.** Get the token with the command from `terraform output -raw admin_token_command`. In the
   app, **Servers → Add server**: *Elsewhere*, `https://<domain>`, that token. The Dashboard then shows every copy.
   Choose the access mode in **Access & API keys** (README section 11.6.7).

10. **Add the books:** upload them in the admin app (**Books → Add book**), or copy many at once and scan:

    ```bash
    aws s3 cp ./my-pdfs/ "$(terraform output -raw pdfs_s3_uri)" --recursive
    ```

    then **Ingestion & jobs → Scan now** (or switch on polling). Fix the guessed titles afterwards.

11. **Tune the scaling.** Run `tests/load_test.py` against the site (README section 11.8) and set
    `requests_per_copy_per_minute` to about 70 % of what one copy sustains, and `search_max` to what you are willing
    to pay for. Consider `waf_search_limit_per_5min` and a per-IP rate limit in the admin app for a public site.

## Everyday operation

* **Update OSISS:** build and push a new image (step 3), then replace the instances without downtime:

  ```bash
  aws autoscaling start-instance-refresh --auto-scaling-group-name <name>-search
  aws autoscaling start-instance-refresh --auto-scaling-group-name <name>-worker
  ```

  Pinning `container_image` to a tag and running `terraform apply` does the same.
* **Logs:** CloudWatch log group `terraform output -raw log_group`, one stream per instance.
* **Backups:** RDS keeps 7 days of automatic backups. The self-managed Elasticsearch node is a single node on its own
  EBS volume: snapshot that volume (AWS Backup or Data Lifecycle Manager), or use Elastic Cloud. The PDFs are in a
  versioned S3 bucket; the index can always be rebuilt from them by re-ingesting.
* **Settings** changed in the admin app reach every copy within seconds (they live in the database). Settings from
  `app_environment` apply on the next instance refresh.

## Elastic Cloud instead of the EC2 node

Create an Elasticsearch 8 deployment in the same AWS region, create an API key that may manage and write the
`osiss_chunks` index, then set:

```hcl
elasticsearch_mode = "elastic_cloud"
elastic_cloud_url  = "https://<deployment>.es.<region>.aws.elastic-cloud.com:443"
# elastic_cloud_api_key: pass it as TF_VAR_elastic_cloud_api_key rather than writing it in a file
```

The key is stored in Secrets Manager with the other secrets. The worker creates the index on first start.

## Removing it

```bash
# 1. allow deletion (and, only if you also want the PDFs and models gone, force_destroy_bucket = true)
#    in terraform.tfvars: deletion_protection = false
terraform apply
# 2. delete everything
terraform destroy
```

A final database snapshot is kept unless `deletion_protection` was already false when the database was created.
An Elastic Cloud deployment is not managed here; delete it in the Elastic console.

## Security and privacy notes

* The books, the search index and the searches are stored in the AWS region you choose. Check that this suits your
  institution's policies and the books' licences; OSISS was designed to run offline on your own servers.
* Only the load balancer is public. The API instances accept traffic only from it, so the client addresses it
  forwards can be trusted for rate limits and the admin lockout.
* Elasticsearch (self-managed) and Redis run without authentication inside the private network, reachable only from
  the API instances' security group. The database requires TLS (`POSTGRES_SSLMODE=require`).
* Secrets are read from Secrets Manager at boot into a root-only file on each instance; they are not in the launch
  templates. They are in the Terraform state, so protect the state.

## What has been verified

`terraform validate` and `terraform test` (`tests/osiss.tftest.hcl`) run the whole configuration against a mocked AWS
provider and check, for both Elasticsearch modes and the optional features, what is created and what each instance
is told (roles, settings, no secrets in user data). The rendered boot scripts are syntax-checked. The configuration
has **not** been applied to a real AWS account as part of this project, so expect to adjust details such as instance
availability in your region, the GPU AMI and quotas on the first run.
