"""
Network architecture diagram (diagram-as-code).

Render:
    pip install diagrams        # also requires Graphviz: brew install graphviz
    python diagrams/architecture.py
Produces diagrams/architecture.png
"""

from diagrams import Cluster, Diagram, Edge
from diagrams.aws.compute import ECS, ECR, Fargate
from diagrams.aws.database import RDS, ElastiCache
from diagrams.aws.devtools import Codepipeline, Codedeploy
from diagrams.aws.integration import Eventbridge
from diagrams.aws.management import Cloudwatch
from diagrams.aws.network import ELB, InternetGateway, Endpoint, VPC
from diagrams.aws.security import IAMRole, SecretsManager
from diagrams.aws.storage import S3
from diagrams.onprem.client import Users
from diagrams.onprem.vcs import Github

graph_attr = {"fontsize": "16", "labelloc": "t"}

with Diagram(
    "To-Do on ECS Fargate - RDS Proxy + ElastiCache (Multi-AZ, NAT-less)",
    filename="diagrams/architecture",
    show=False,
    direction="LR",
    graph_attr=graph_attr,
):
    users = Users("Users")

    with Cluster("CI/CD (OIDC, no long-lived keys)"):
        gh = Github("App repo\nGitHub Actions")
        ecr = ECR("ECR")
        evt = Eventbridge("EventBridge\n(ECR push)")
        pipe = Codepipeline("CodePipeline")
        deploy = Codedeploy("CodeDeploy\nblue/green")
        gh >> Edge(label="build & push (OIDC)") >> ecr
        ecr >> evt >> pipe >> deploy

    with Cluster("IAM (dedicated stack)"):
        task_roles = IAMRole("ECS task +\nexecution roles")
        cicd_roles = IAMRole("CodeDeploy / pipeline /\nEventBridge roles")

    with Cluster("VPC 10.0.0.0/16 (2 AZs)"):
        igw = InternetGateway("IGW")

        with Cluster("Public subnets"):
            alb = ELB("Public ALB :80")

        with Cluster("App subnets (private)"):
            svc = ECS("ECS Service")
            tasks = [Fargate("task AZ1"), Fargate("task AZ2")]
            with Cluster("VPC endpoints"):
                eps = Endpoint("ECR / Logs /\nSecrets / SSM + S3 gw")

        with Cluster("Data subnets (private)"):
            proxy = RDS("RDS Proxy")
            db = RDS("PostgreSQL\nMulti-AZ")

        with Cluster("Cache subnets (private)"):
            redis = ElastiCache("Redis\n(primary+replica)")

        logs = Cloudwatch("CloudWatch Logs")
        secret = SecretsManager("DB secret")

        users >> igw >> alb >> Edge(label="HTTP") >> svc
        svc >> tasks
        for t in tasks:
            t >> Edge(label="writes") >> proxy
            t >> Edge(label="reads (cache)") >> redis
            t >> Edge(style="dashed") >> eps
        proxy >> db
        proxy >> Edge(style="dashed", label="creds") >> secret
        eps >> Edge(style="dashed") >> logs
        eps >> Edge(style="dashed") >> ecr

    deploy >> Edge(label="shift traffic\nblue -> green") >> svc

    # service roles now provisioned by the dedicated iam stack
    svc >> Edge(style="dashed", label="assumes") >> task_roles
    pipe >> Edge(style="dashed", label="assumes") >> cicd_roles
    deploy >> Edge(style="dashed", label="assumes") >> cicd_roles
