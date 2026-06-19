# Architecture (Mermaid)

```mermaid
flowchart LR
  users([Users])

  subgraph CICD["CI/CD — OIDC, no long-lived keys"]
    gh["App repo<br/>GitHub Actions"]
    ecr[("ECR")]
    evt["EventBridge<br/>(ECR push)"]
    pipe["CodePipeline"]
    cd["CodeDeploy<br/>blue/green"]
    gh -->|build & push via OIDC| ecr
    ecr --> evt --> pipe --> cd
  end

  subgraph IAM["IAM — dedicated stack"]
    taskroles["ECS task +<br/>execution roles"]
    cicdroles["CodeDeploy / CodePipeline /<br/>EventBridge roles"]
  end

  subgraph VPC["VPC 10.0.0.0/16 — 2 AZs, NAT-less"]
    igw["Internet Gateway"]

    subgraph PUB["Public subnets (az1/az2)"]
      alb["Public ALB :80<br/>blue + green target groups"]
    end

    subgraph APP["App subnets — private (az1/az2)"]
      ecs["ECS Fargate Service<br/>autoscale 1→4 on CPU"]
      subgraph EP["VPC endpoints"]
        eps["ECR api/dkr · Logs · Secrets · SSM<br/>+ S3 gateway"]
      end
    end

    subgraph DATA["Data subnets — private (az1/az2)"]
      proxy["RDS Proxy"]
      rds[("RDS PostgreSQL<br/>Multi-AZ, db.t3")]
    end

    subgraph CACHE["Cache subnets — private (az1/az2)"]
      redis[("ElastiCache Redis<br/>primary + replica")]
    end

    logs["CloudWatch Logs"]
    secret["Secrets Manager<br/>DB credentials"]
  end

  users --> igw --> alb -->|HTTP| ecs
  ecs -->|writes| proxy --> rds
  proxy -. reads creds .-> secret
  ecs -->|reads / cache| redis
  ecs -. 443 .-> eps
  eps -. logs .-> logs
  eps -. pull image .-> ecr
  cd -->|shift traffic blue→green| ecs
  ecs -. assumes .-> taskroles
  pipe -. assumes .-> cicdroles
  cd -. assumes .-> cicdroles
```

> The RDS Proxy role stays in the data stack (its policy needs the
> auto-generated DB secret ARN) and the GitHub OIDC roles live in the bootstrap
> stack; every other service role is centralized in the **iam** stack.

## Security group matrix (least privilege)

| Source | Destination | Port | Purpose |
|--------|-------------|------|---------|
| Internet | ALB SG | 80 | Public HTTP |
| ALB SG | ECS SG | 8080 | Forward + health checks |
| ECS SG | RDS Proxy SG | 5432 | DB writes via proxy |
| ECS SG | Redis SG | 6379 | Cache reads/writes |
| ECS SG | Endpoints SG | 443 | ECR/Logs/Secrets/SSM |
| ECS SG | S3 prefix list | 443 | ECR image layers (S3 gw) |
| RDS Proxy SG | RDS SG | 5432 | Proxy → instance |
| VPC CIDR | Endpoints SG | 443 | Interface endpoints |

Every other path is denied by default. RDS accepts traffic **only** from the
RDS Proxy SG; Redis accepts traffic **only** from the ECS SG.
