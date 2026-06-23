# 法務文書 RAG アプリケーション

機密性を要する法務文書(契約書)を対象に、**コスト最小化・低設計負荷**の制約下で構築した、
サーバーレス RAG(Retrieval-Augmented Generation)アプリケーションです。AWS のマネージド
サービスを中心に、認証・監査・自動取り込み・PII マスキング・品質評価・IaC までを段階的に
実装しました。

> **公開にあたっての注記**
> 本リポジトリはポートフォリオとして構成を「見せる」ことを目的としています。
> アカウント ID・エンドポイント URL・リソース ID 等の固有値は、すべて
> `<PLACEHOLDER>` 形式に置換しています。稼働中のフロントエンド URL は掲載していません。

# Legal Document RAG Application

A serverless RAG (Retrieval-Augmented Generation) application built for confidential legal documents (contracts), designed under the constraints of **cost minimization and low design overhead**. Using AWS managed services as the foundation, authentication, auditing, automated ingestion, PII masking, quality evaluation, and IaC were implemented in stages.

> **Note on Publication**
> This repository is intended to showcase the system's structure as a portfolio.
> All unique values such as account IDs, endpoint URLs, and resource IDs have been
> replaced with the `<PLACEHOLDER>` format. The live frontend URL is not included.

---

## 設計思想

技術選択は、一貫した優先順位に従って決定しました。

**コスト最小化 → 設計負荷最小化 → 機密性 → 品質 → レイテンシ**

この優先順位は、個別の技術判断を実際に規定しています。例えばベクトルストアに S3 Vectors を
採用し OpenSearch Serverless を退けたのはアイドル課金の回避(コスト)であり、トークン検証を
In-Lambda の軽量方式としたのは設計負荷最小化です。

## Design Philosophy

Technical decisions were made according to a consistent set of priorities.

**Cost minimization → Design overhead minimization → Confidentiality → Quality → Latency**

This priority order actually governed individual technical decisions. For example, adopting S3 Vectors as the vector store over OpenSearch Serverless was to avoid idle billing (cost), and using a lightweight In-Lambda token validation approach was to minimize design overhead.

---

## 主な機能

- **認証**: Amazon Cognito(Hosted UI + 認可コードフロー + PKCE)による認証。Lambda 内で
  ID トークンを検証(RS256 固定、aud/iss/exp/token_use の厳格化)。
- **監査ログ**: 全リクエスト(利用者 ID・質問・回答・引用・タイムスタンプ)を S3 に記録。
- **RAG**: Amazon Bedrock Knowledge Bases によるマネージド検索・生成。引用付き回答。
- **自動取り込み**: ブラウザから presigned URL で S3 へ直接アップロード → S3 イベントで
  取り込みを自動起動。
- **PII マスキング**: 取り込み前に、正規表現ベースの限定マスキング(メール・電話・カード番号)。
- **品質評価**: Bedrock RAG 評価(LLM-as-a-judge)による定量的な品質測定基盤。
- **IaC**: AWS CDK(Python)による再現可能なインフラ定義。

## Key Features

- **Authentication**: Authentication via Amazon Cognito (Hosted UI + authorization code flow + PKCE). ID tokens are validated within Lambda (RS256 fixed, strict aud/iss/exp/token_use checks).
- **Audit Logging**: All requests (user ID, question, answer, citations, timestamp) are recorded in S3.
- **RAG**: Managed retrieval and generation via Amazon Bedrock Knowledge Bases. Answers include citations.
- **Automated Ingestion**: Direct upload from browser to S3 via presigned URL → ingestion automatically triggered by S3 events.
- **PII Masking**: Limited regex-based masking (email, phone, card numbers) applied before ingestion.
- **Quality Evaluation**: Quantitative quality measurement infrastructure using Bedrock RAG evaluation (LLM-as-a-judge).
- **IaC**: Reproducible infrastructure definition using AWS CDK (Python).

---

## アーキテクチャ

構成図は [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) を参照してください。

主要コンポーネント:

| 層 | 構成 |
|---|---|
| UI 配信 | CloudFront + OAC(非公開 S3 オリジン) |
| 認証 | Cognito User Pool + public client(PKCE)+ Hosted UI |
| API | Lambda Function URL(アプリ層でトークン検証) |
| RAG | Bedrock Knowledge Bases + S3 Vectors + Titan Embeddings V2 |
| 生成モデル | Claude Sonnet(日本国内推論に限定) |
| 監査 | 専用 S3 バケット(暗号化・バージョニング・ライフサイクル) |

### データレジデンシの非対称設計

保存は東京リージョンに完全固定する一方、生成モデルが東京 In-Region 非対応のため、推論のみ
日本国内(東京・大阪)に限定するクロスリージョン推論プロファイルを用いています。
「保存=固定/推論=国内」という非対称性を全体で一貫させています。

## Architecture

For the system diagram, refer to [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Key components:

| Layer | Configuration |
|---|---|
| UI Delivery | CloudFront + OAC (private S3 origin) |
| Authentication | Cognito User Pool + public client (PKCE) + Hosted UI |
| API | Lambda Function URL (token validation at application layer) |
| RAG | Bedrock Knowledge Bases + S3 Vectors + Titan Embeddings V2 |
| Generative Model | Claude Sonnet (restricted to Japan domestic inference) |
| Auditing | Dedicated S3 bucket (encryption, versioning, lifecycle) |

### Asymmetric Data Residency Design

While storage is fully fixed to the Tokyo region, the generative model does not support Tokyo In-Region inference, so a cross-region inference profile limited to within Japan (Tokyo and Osaka) is used for inference only. The asymmetry of "storage = fixed / inference = domestic" is maintained consistently throughout.

---

## リクエストのライフサイクル

**質問**: ログイン(PKCE)→ ID トークン取得 → API へ Bearer 送出 → Lambda が検証 →
利用者確定 → Bedrock KB が検索・生成 → 引用付き回答 → 監査ログ記録。

**アップロード**: presigned URL 要求 → S3 へ直接 PUT → S3 イベントで Lambda 起動 →
PII マスキング → 書き戻し → 取り込み起動 → 数分後に検索対象として反映。

## Request Lifecycle

**Query**: Login (PKCE) → Obtain ID token → Send Bearer to API → Lambda validates → Identify user → Bedrock KB retrieves and generates → Response with citations → Audit log recorded.

**Upload**: Request presigned URL → Direct PUT to S3 → Lambda triggered by S3 event → PII masking → Write back → Trigger ingestion → Available for search after a few minutes.

---

## 実装フェーズ

段階的なロードマップに沿って実装しました。各フェーズの詳細レポートを `reports/` に収録しています。

| フェーズ | 内容 | レポート |
|---|---|---|
| S0 | データレジデンシ是正・予算統制 | (S1 レポート内で言及) |
| S1 | 認証正常化・利用者識別・監査ログ・UI | [reports/S1.md](reports/S1.md) |
| S2 | 自動取り込み・UI アップロード | [reports/S2.md](reports/S2.md) |
| S3 | PII マスキング | [reports/S3.md](reports/S3.md) |
| S4 | 評価駆動の品質改善 | [reports/S4.md](reports/S4.md) |
| S5 | 運用整備・IaC | [reports/S5.md](reports/S5.md) |
| 総括 | プロジェクト全体の総括 | [reports/FINAL.md](reports/FINAL.md) |

## Implementation Phases

Implemented in stages following a phased roadmap. Detailed reports for each phase are stored in `reports/`.

| Phase | Content | Report |
|---|---|---|
| S0 | Data residency correction, budget controls | (Mentioned in S1 report) |
| S1 | Authentication normalization, user identification, audit logging, UI | [reports/S1.md](reports/S1.md) |
| S2 | Automated ingestion, UI upload | [reports/S2.md](reports/S2.md) |
| S3 | PII masking | [reports/S3.md](reports/S3.md) |
| S4 | Evaluation-driven quality improvement | [reports/S4.md](reports/S4.md) |
| S5 | Operations setup, IaC | [reports/S5.md](reports/S5.md) |
| Summary | Overall project summary | [reports/FINAL.md](reports/FINAL.md) |

---

## 設計上の特徴的な意思決定

このプロジェクトでは、いくつかの判断を測定と論拠に基づいて行いました。一例:

- **reranker を測定の上で見送り**: reranker 導入をベースラインと定量比較した結果、忠実性・
  引用精度は改善する一方で網羅性が大きく低下するトレードオフが判明。法務文書における横断比較
  (複数契約の比較)で必要な文書を絞り込みすぎることを個別分析で特定し、採用を見送りました。
  測定により品質低下を未然に回避した事例です。
- **Comprehend 制約への対応**: 当初想定した Amazon Comprehend の PII 検出が日本語非対応で
  あることが判明。限定マスキングという方針と組み合わせ、外部依存のない正規表現方式へ転換
  しました。
- **単一スタックへの収束**: IaC のスタック分割が循環参照を招いたため、小規模・密結合という
  実態に即して単一スタックへ収束させました。

詳細は [reports/FINAL.md](reports/FINAL.md) を参照してください。

## Notable Design Decisions

In this project, several decisions were made based on measurement and reasoning. Examples:

- **Reranker rejected after measurement**: A quantitative comparison of reranker introduction against the baseline revealed a trade-off: while fidelity and citation accuracy improved, recall dropped significantly. Individual analysis identified that cross-contract comparison queries (comparing multiple contracts) suffered from over-filtering, and the reranker was rejected. An example of preventing quality degradation through measurement.
- **Addressing Comprehend limitations**: Amazon Comprehend's PII detection, which was originally planned, turned out not to support Japanese. Combined with the policy of limited masking, this led to a switch to a regex-based approach with no external dependencies.
- **Convergence to a single stack**: Splitting the IaC stacks led to circular references, so the architecture was converged to a single stack to match the reality of small scale and tight coupling.

For details, refer to [reports/FINAL.md](reports/FINAL.md).

---

## ディレクトリ構成

```
.
├── README.md
├── SECURITY.md              # セキュリティ設計と公開時のサニタイズ方針
├── docs/
│   └── ARCHITECTURE.md      # アーキテクチャ図(Mermaid)
├── reports/                 # 各フェーズ・全体の詳細レポート
│   ├── S1.md ... S5.md
│   └── FINAL.md
├── src/
│   ├── lambda_function.py   # Lambda(認証・RAG・アップロード・マスキング・監査)
│   └── index.html           # 単一ファイル SPA(固有値はプレースホルダ)
└── infra/                   # AWS CDK(Python)
    ├── app.py
    └── rag_cdk/rag_stack.py
```

## Directory Structure

```
.
├── README.md
├── SECURITY.md              # Security design and sanitization policy for publication
├── docs/
│   └── ARCHITECTURE.md      # Architecture diagram (Mermaid)
├── reports/                 # Detailed reports for each phase and the overall project
│   ├── S1.md ... S5.md
│   └── FINAL.md
├── src/
│   ├── lambda_function.py   # Lambda (authentication, RAG, upload, masking, auditing)
│   └── index.html           # Single-file SPA (unique values replaced with placeholders)
└── infra/                   # AWS CDK (Python)
    ├── app.py
    └── rag_cdk/rag_stack.py
```

---

## 技術スタック

Amazon Bedrock(Knowledge Bases / RAG 評価)・S3 Vectors・Titan Embeddings V2・
AWS Lambda(Python 3.13)・Amazon Cognito・Amazon CloudFront・Amazon S3・
AWS CDK(Python)・PyJWT。

## Technology Stack

Amazon Bedrock (Knowledge Bases / RAG Evaluation) · S3 Vectors · Titan Embeddings V2 · AWS Lambda (Python 3.13) · Amazon Cognito · Amazon CloudFront · Amazon S3 · AWS CDK (Python) · PyJWT.

---

## ライセンス

本リポジトリは閲覧・参照を目的として公開しています。明示的なライセンスは付与していません
(全権利を留保します)。コードやドキュメントの再利用を希望される場合はご連絡ください。

## License

This repository is published for viewing and reference purposes. No explicit license is granted (all rights reserved). If you wish to reuse the code or documentation, please contact us.

# AWS-RAG-system
