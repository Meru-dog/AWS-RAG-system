"""RagStack:RAG アプリケーション全体を単一スタックで再現する。

A方針(再現可能性の確立)。小規模かつリソースが密結合(文書バケットが CloudFront・
Lambda・イベント通知と多方向に結合)のため、単一スタックが最も素直で循環が起きない。

含むもの:
- 監査ログバケット / 文書バケット
- Cognito User Pool / public client(PKCE)/ Hosted ドメイン
- CloudFront + OAC(文書バケットを非公開配信)
- Lambda(rag-document)+ PyJWT レイヤー + Function URL(CORS)
- 実行 IAM ロール(Bedrock 生成・取り込み / S3 PutObject / 監査ログ)
- S3 イベント通知(documents/ → Lambda で取り込み起動)
- Knowledge Base は既存リソースを ID 参照(KB 自体の再現は申し送り)

【申し送り 1:Managed ログイン スタイル】
再現時、Cognito コンソールで rag-spa-public へスタイルを手動割り当てが必要
(欠くと "Login pages unavailable")。

【申し送り 2:Knowledge Base】
本スタックは KB を作成しない。既存 KB(rag-document-v2 / <KNOWLEDGE_BASE_ID>)、Data Source
(<DATA_SOURCE_ID>)、S3 Vectors、Titan Embeddings は別途手順で構築し、その ID を引数で渡す。
"""
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_kms as kms,
    aws_cognito as cognito,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_lambda as lambda_,
    aws_iam as iam,
)
from constructs import Construct


class RagStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cloudfront_origin: str = "https://<CLOUDFRONT_DOMAIN>",
        audit_bucket_name: str = "<AUDIT_LOG_BUCKET>",
        document_bucket_name: str = "rag-document",
        domain_prefix: str = "<COGNITO_DOMAIN_PREFIX>",
        audit_ia_transition_days: int = 90,
        audit_expiration_days: int = 1095,   # 3年
        use_cmk_for_audit: bool = False,
        knowledge_base_id: str = "<KNOWLEDGE_BASE_ID>",
        data_source_id: str = "<DATA_SOURCE_ID>",
        model_arn: str = "arn:aws:bedrock:ap-northeast-1:<AWS_ACCOUNT_ID>:inference-profile/jp.anthropic.claude-sonnet-4-6",
        account_id: str = "<AWS_ACCOUNT_ID>",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        region = self.region

        # ============ 監査ログバケット ============
        audit_kms_key = None
        audit_encryption = s3.BucketEncryption.S3_MANAGED
        if use_cmk_for_audit:
            audit_kms_key = kms.Key(
                self, "AuditLogKmsKey",
                description="CMK for RAG audit log bucket",
                enable_key_rotation=True,
                removal_policy=RemovalPolicy.RETAIN,
            )
            audit_encryption = s3.BucketEncryption.KMS

        audit_bucket = s3.Bucket(
            self, "AuditLogBucket",
            bucket_name=audit_bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=audit_encryption,
            encryption_key=audit_kms_key,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="audit-retention",
                    enabled=True,
                    prefix="audit/",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(audit_ia_transition_days),
                        )
                    ],
                    expiration=Duration.days(audit_expiration_days),
                )
            ],
        )

        # ============ 文書バケット ============
        document_bucket = s3.Bucket(
            self, "DocumentBucket",
            bucket_name=document_bucket_name,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            versioned=False,
            removal_policy=RemovalPolicy.RETAIN,
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=[cloudfront_origin],
                    allowed_headers=["*"],
                    max_age=300,
                )
            ],
        )

        # ============ Cognito ============
        user_pool = cognito.UserPool(
            self, "UserPool",
            user_pool_name="rag-user-pool",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True, email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            password_policy=cognito.PasswordPolicy(
                min_length=8, require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=True,
            ),
        )
        user_pool.add_domain(
            "HostedDomain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=domain_prefix),
        )
        app_client = user_pool.add_client(
            "SpaPublicClient",
            user_pool_client_name="rag-spa-public",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True),
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[
                    cognito.OAuthScope.OPENID,
                    cognito.OAuthScope.EMAIL,
                    cognito.OAuthScope.PROFILE,
                ],
                callback_urls=[f"{cloudfront_origin}/"],
                logout_urls=[f"{cloudfront_origin}/"],
            ),
            supported_identity_providers=[
                cognito.UserPoolClientIdentityProvider.COGNITO
            ],
            prevent_user_existence_errors=True,
        )

        # ============ CloudFront + OAC ============
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(document_bucket)
        distribution = cloudfront.Distribution(
            self, "Distribution",
            default_root_object="index.html",
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD,
            ),
            comment="RAG document UI distribution (OAC, private S3 origin)",
        )

        # ============ Lambda(レイヤー / ロール / 関数) ============
        pyjwt_layer = lambda_.LayerVersion(
            self, "PyJwtLayer",
            code=lambda_.Code.from_asset("lambda/layer"),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_13],
            description="PyJWT[crypto] for Cognito ID token verification",
        )

        role = iam.Role(
            self, "RagLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:RetrieveAndGenerate", "bedrock:Retrieve",
                     "bedrock:StartIngestionJob"],
            resources=[f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/{knowledge_base_id}"],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel"],
            resources=[
                model_arn,
                f"arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6*",
            ],
        ))
        document_bucket.grant_put(role, "documents/*")
        document_bucket.grant_read(role, "documents/*")   # S3: PII マスキングで文書を読む
        audit_bucket.grant_put(role, "audit/*")

        fn = lambda_.Function(
            self, "RagFunction",
            function_name="rag-document",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=lambda_.Architecture.X86_64,
            handler="lambda_function.handler",
            code=lambda_.Code.from_asset("lambda/rag_document"),
            layers=[pyjwt_layer],
            role=role,
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "KNOWLEDGE_BASE_ID": knowledge_base_id,
                "DATA_SOURCE_ID": data_source_id,
                "MODEL_ARN": model_arn,
                "COGNITO_USER_POOL_ID": user_pool.user_pool_id,
                "COGNITO_APP_CLIENT_ID": app_client.user_pool_client_id,
                "AUDIT_BUCKET": audit_bucket.bucket_name,
                "DOCUMENT_BUCKET": document_bucket.bucket_name,
                "CORS_ALLOW_ORIGIN": cloudfront_origin,
            },
        )

        fn_url = fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=[cloudfront_origin],
                allowed_methods=[lambda_.HttpMethod.POST],
                allowed_headers=["authorization", "content-type"],
                max_age=Duration.seconds(300),
            ),
        )

        # ============ S3 イベント通知(同一スタック内なので循環しない) ============
        document_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(fn),
            s3.NotificationKeyFilter(prefix="documents/"),
        )

        # ============ 出力 ============
        CfnOutput(self, "DocumentBucketName", value=document_bucket.bucket_name)
        CfnOutput(self, "AuditBucketName", value=audit_bucket.bucket_name)
        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "AppClientId", value=app_client.user_pool_client_id)
        CfnOutput(self, "HostedUiBaseUrl",
                  value=f"https://{domain_prefix}.auth.{region}.amazoncognito.com")
        CfnOutput(self, "DistributionId", value=distribution.distribution_id)
        CfnOutput(self, "DistributionDomainName", value=distribution.distribution_domain_name)
        CfnOutput(self, "FunctionUrl", value=fn_url.url)
