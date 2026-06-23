#!/usr/bin/env python3
"""RAG アプリケーションの IaC エントリポイント(A方針:再現可能性の確立)。

単一スタック構成。小規模かつリソースが密結合のため、分割せず 1 スタックに集約する。
これにより OAC・S3 イベント通知などの後付け設定でも循環参照が起きない。東京リージョン固定。
"""
import aws_cdk as cdk

from rag_cdk.rag_stack import RagStack

ENV = cdk.Environment(account="<AWS_ACCOUNT_ID>", region="ap-northeast-1")

app = cdk.App()

RagStack(
    app,
    "RagStack",
    env=ENV,
    audit_ia_transition_days=90,
    audit_expiration_days=1095,   # 3年
    use_cmk_for_audit=False,      # 既定 SSE-S3。CMK に切り替える場合は True
    knowledge_base_id="<KNOWLEDGE_BASE_ID>",
    data_source_id="<DATA_SOURCE_ID>",
)

app.synth()
