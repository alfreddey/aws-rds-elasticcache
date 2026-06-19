# To-Do Platform — Infrastructure (CloudFormation)

Highly available, secure infrastructure for a containerized Java To-Do app on
**Amazon ECS Fargate**, with **Amazon RDS PostgreSQL** (Multi-AZ) behind **RDS
Proxy**, **ElastiCache for Redis** as a read cache, a public **Application Load
Balancer**, and a **blue/green** delivery pipeline (CodePipeline + CodeDeploy)
triggered by **EventBridge** on ECR image pushes. CI/CD authenticates to AWS via
**GitHub OIDC** — no long-lived credentials. Everything is provisioned with
**CloudFormation** and deployed via **CloudFormation Git sync**.

> This repository contains **infrastructure only**. The Java application,
> `Dockerfile`, and its build workflow live in a separate application repository.
> The reference `appspec/` files and app workflow snippet below show the contract
> between the two.

## Architecture

See [`diagrams/architecture.md`](diagrams/architecture.md) for the rendered
Mermaid diagram and security-group matrix, or run
[`diagrams/architecture.py`](diagrams/architecture.py) to produce a PNG.

```
Internet ─▶ Public ALB ─▶ ECS Fargate (private app subnets, autoscale 1→4)
                               │
                               ├─ writes ─▶ RDS Proxy ─▶ RDS PostgreSQL (Multi-AZ, private data subnets)
                               ├─ reads  ─▶ ElastiCache Redis (private cache subnets)
                               └─ 443    ─▶ VPC endpoints: ECR · Logs · Secrets · SSM (+ S3 gateway)

GitHub Actions ─(OIDC)─▶ ECR ─▶ EventBridge ─▶ CodePipeline ─▶ CodeDeploy (blue/green) ─▶ ECS
```

### Key design decisions

| Decision | Choice | Why |
|----------|--------|-----|
| CFN organization | Nested stacks (root → network/security/data/iam/compute/pipeline) via Git sync | Clear tier separation, one deployment unit |
| IAM organization | App + CI/CD service roles centralized in a dedicated **iam** stack | One auditable place for roles; RDS Proxy role stays in `data.yaml` (its policy needs the secret ARN) |
| Network egress | **NAT-less**, fully private | No NAT cost; tasks reach AWS only via VPC endpoints |
| Database | RDS **PostgreSQL Multi-AZ**, `db.t3.micro`, RDS Proxy | Matches brief; standby failover; pooled connections |
| Subnet design | 4 dedicated tiers × 2 AZs = 8 subnets | App / data / cache fully segregated per the brief |
| Foundational resources | ECR, OIDC roles, deploy-config bucket in a **bootstrap** stack | Lets the first image exist before the ECS service is created |

## Repository layout

```
.
├── bootstrap/bootstrap.yaml        # one-time: template bucket, OIDC provider+roles, ECR, config bucket
├── templates/
│   ├── root.yaml                   # nested-stack orchestrator (Git sync target)
│   ├── network.yaml                # VPC, 8 subnets, routing, VPC endpoints, S3 prefix-list lookup
│   ├── security.yaml               # one least-privilege SG per tier
│   ├── data.yaml                   # RDS PostgreSQL Multi-AZ, RDS Proxy (+ its role), ElastiCache Redis, secret
│   ├── iam.yaml                    # ECS task/execution roles + CodeDeploy/CodePipeline/EventBridge roles
│   ├── compute.yaml                # ALB + blue/green TGs, ECS cluster/task/service, autoscaling, logs
│   └── pipeline.yaml               # CodePipeline, CodeDeploy blue/green, EventBridge trigger
├── gitsync/deployment.yaml         # CloudFormation Git sync deployment file (params + tags)
├── params/parameters.sample.json   # parameters for a manual CLI deploy (alternative to Git sync)
├── appspec/                        # reference appspec.yaml + taskdef.json for the APP repo
├── .github/workflows/sync-templates.yml   # OIDC: mirror templates/ to S3 on push
└── diagrams/                       # diagram-as-code + Mermaid
```

## Prerequisites

- AWS account + AWS CLI v2, with permissions to create IAM/VPC/ECS/RDS/etc.
- A GitHub org/user owning **this** infra repo and the **app** repo.
- Region with ≥ 2 AZs (default examples use `us-east-1`).

## Deploy

Replace `123456789012`, `your-org`, repo names, and `us-east-1` as needed.

### 1. Bootstrap (once, via CLI)

Creates the template bucket, GitHub OIDC provider, both OIDC roles, the ECR
repository, and the deploy-config bucket.

```bash
aws cloudformation deploy \
  --template-file bootstrap/bootstrap.yaml \
  --stack-name todo-app-prod-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      ProjectName=todo-app Environment=prod \
      GitHubOrg=your-org \
      GitHubInfraRepo=aws-rds-redis \
      GitHubAppRepo=todo-app

# Note the outputs — you'll need TemplateBucketName, InfraSyncRoleArn,
# GitHubActionsAppRoleArn, EcrRepositoryUri:
aws cloudformation describe-stacks --stack-name todo-app-prod-bootstrap \
  --query 'Stacks[0].Outputs' --output table
```

> If your account already has a GitHub OIDC provider, pass
> `ExistingOIDCProviderArn=arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com`.

### 2. Push the first application image

In the **app repo**, configure OIDC and push an image tagged `latest` to ECR so
the ECS service has something to run on first creation. Minimal build workflow:

```yaml
# .github/workflows/build.yml  (in the APPLICATION repo)
permissions: { id-token: write, contents: read }
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::123456789012:role/todo-app-prod-gha-app
          aws-region: us-east-1
      - uses: aws-actions/amazon-ecr-login@v2
        id: ecr
      - name: Build & push
        env:
          IMAGE: 123456789012.dkr.ecr.us-east-1.amazonaws.com/todo-app-prod
        run: |
          docker build -t "$IMAGE:latest" -t "$IMAGE:${GITHUB_SHA::7}" .
          docker push "$IMAGE:latest"
          docker push "$IMAGE:${GITHUB_SHA::7}"
```

### 3. Mirror templates to S3

In **this** infra repo, set Actions *Variables*: `AWS_REGION`, `TEMPLATE_BUCKET`
(= bootstrap `TemplateBucketName`), `INFRA_SYNC_ROLE_ARN` (= bootstrap
`InfraSyncRoleArn`). Push to `main` (or run the workflow manually) so
[`sync-templates.yml`](.github/workflows/sync-templates.yml) validates and copies
`templates/*.yaml` into the bucket. CloudFormation Git sync reads the nested
children from there.

### 4. Enable CloudFormation Git sync on the root stack

1. Edit [`gitsync/deployment.yaml`](gitsync/deployment.yaml): set `TemplateBucket`
   to the bootstrap `TemplateBucketName`.
2. CloudFormation console → **Create stack** → **Sync from Git**.
3. Create/select a **CodeConnections** connection to GitHub, choose this repo +
   `main`.
4. Template file: `templates/root.yaml`; deployment file: `gitsync/deployment.yaml`.
5. IAM role: allow CloudFormation to manage these resources
   (`CAPABILITY_NAMED_IAM` equivalent).
6. Merge the PR Git sync opens. CloudFormation provisions the full platform and
   re-syncs on every future commit to `main`.

### 5. Get the ALB endpoint

```bash
aws cloudformation describe-stacks --stack-name <root-stack-name> \
  --query "Stacks[0].Outputs[?OutputKey=='AlbUrl'].OutputValue" --output text
```

Open that URL — the app is served by the initial task definition pulling
`:latest` from ECR.

## Blue/green deployments (steady state)

Once the platform is live, every push to the app repo's default branch should:

1. Build + push a new image to ECR (`latest` + SHA tag).
2. Render `taskdef.json` from the live platform outputs and zip it with
   `appspec.yaml` into `config.zip`, then upload to the deploy-config bucket.
3. The ECR push fires the **EventBridge** rule → **CodePipeline** starts →
   **CodeDeploy** registers a green task set, health-checks it on the green
   target group, shifts the ALB listener from blue → green, then terminates blue
   after 5 minutes. Failures auto-roll back.

App-repo deploy step (extends the build workflow above):

```yaml
      - name: Render taskdef & upload deploy config
        env:
          STACK: todo-app-prod            # the Git sync root stack name
          BUCKET: todo-app-prod-deploy-config-123456789012
          REGION: us-east-1
        run: |
          out() { aws cloudformation describe-stacks --stack-name "$STACK" \
            --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
          # the platform stack also exposes nested outputs via its own outputs;
          # for nested values, read them from the child stacks or SSM as you prefer.
          sed -e "s#<IMAGE1_NAME>#<IMAGE1_NAME>#g" \
              -e "s#<EXECUTION_ROLE_ARN>#$(out TaskExecutionRoleArn)#g" \
              -e "s#<TASK_ROLE_ARN>#$(out TaskRoleArn)#g" \
              -e "s#<DB_PROXY_ENDPOINT>#$(out DBProxyEndpoint)#g" \
              -e "s#<REDIS_PRIMARY_ENDPOINT>#$(out RedisPrimaryEndpoint)#g" \
              -e "s#<DB_SECRET_ARN>#$(out DBSecretArn)#g" \
              -e "s#<AWS_REGION>#$REGION#g" \
              appspec/taskdef.json > taskdef.json
          zip config.zip taskdef.json appspec/appspec.yaml
          aws s3 cp config.zip "s3://$BUCKET/config.zip"
```

> Keep `<IMAGE1_NAME>` literal in `taskdef.json` — CodePipeline replaces it with
> the pushed image URI (`Image1ContainerName: IMAGE1_NAME`). Surface the nested
> stack outputs you need (proxy/redis/secret/roles) as root outputs or SSM
> parameters so the app workflow can read them.

## Application runtime contract

The container receives:

| Env var | Source | Notes |
|---------|--------|-------|
| `DB_HOST` | RDS Proxy endpoint | connect with TLS (proxy `RequireTLS`) |
| `DB_PORT` | `5432` | |
| `DB_NAME` | `todos` | |
| `DB_USERNAME` / `DB_PASSWORD` | Secrets Manager (injected) | never in plaintext |
| `REDIS_HOST` / `REDIS_PORT` | ElastiCache primary endpoint | reads cached here |

App must expose the health path (`/actuator/health` by default) on the container
port (`8080`) for ALB health checks. Pattern: read-through cache (check Redis →
miss → read RDS via proxy → populate Redis); writes go to RDS via the proxy and
invalidate/update the cache.

## Operations

- **Autoscaling**: ECS service min 1 / desired 1 / max 4, target-tracking on 60%
  average CPU (`compute.yaml`).
- **Logs**: `/ecs/todo-app-prod` in CloudWatch Logs (30-day retention); Container
  Insights enabled on the cluster.
- **Debugging**: ECS Exec is enabled (via the `ssmmessages` endpoint) —
  `aws ecs execute-command ...`.

## Security highlights

- Tasks, RDS, proxy and Redis run only in **private** subnets with **no NAT** and
  no public IPs; all AWS access is via VPC endpoints.
- **One SG per tier**, each scoped to a single source SG/port (see the matrix in
  the diagram). RDS only accepts the proxy; Redis only accepts ECS.
- DB credentials are generated into **Secrets Manager** and injected; the proxy
  reads them through a scoped role; RDS Proxy enforces **TLS**.
- ECR scan-on-push, S3 buckets are encrypted + fully public-access-blocked, RDS
  and Redis encrypted at rest, RDS storage encrypted, IAM DB auth available.
- CI/CD uses **GitHub OIDC** with repo-scoped trust — no static keys.

## Cost notes

NAT-less design avoids ~\$32/AZ/mo NAT charges. Interface endpoints (~\$7/mo each)
are the main fixed network cost. `db.t3.micro` Multi-AZ + `cache.t3.micro` keep
data-tier cost low. Pipeline artifact/config buckets are negligible.

## Teardown

```bash
# 1. Delete the Git sync root stack (console: stop sync, then delete) — this
#    removes the 6 nested stacks. RDS takes a final snapshot (DeletionPolicy).
# 2. Empty + delete the artifact/config/template buckets if retained.
# 3. Delete the bootstrap stack:
aws cloudformation delete-stack --stack-name todo-app-prod-bootstrap
```

## Rubric coverage

| Requirement | Where |
|-------------|-------|
| Multi-AZ VPC, correct subnet design | `network.yaml` (4 tiers × 2 AZs) |
| Private ECS + VPC endpoints + public ALB | `network.yaml`, `compute.yaml` |
| RDS / Proxy / Redis segregated subnets + distinct SGs | `data.yaml`, `security.yaml` |
| Provisioned via CloudFormation Git sync | `templates/`, `gitsync/deployment.yaml` |
| GitHub Actions builds image / pushes to ECR | app-repo workflow (snippet above) |
| OIDC for AWS auth | `bootstrap.yaml` (provider + roles) |
| EventBridge detects push → CodeDeploy | `pipeline.yaml` |
| Accessible via ALB / health checks / CW logs | `compute.yaml` |
| Autoscaling 1–4 | `compute.yaml` |
| Blue/green deployment | `pipeline.yaml` (CodeDeploy ECS) |
| Tagging / security / cost best practices | stack tags + design above |
