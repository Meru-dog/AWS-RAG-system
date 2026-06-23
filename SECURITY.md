# セキュリティ設計

本ドキュメントは、本プロジェクトのセキュリティ上の設計判断と、公開リポジトリとしての
サニタイズ方針を記述します。法務文書という機密性の高いデータを扱う前提に立ち、各層で
防御を設計しています。

# Security Design

This document describes the security design decisions for this project and the sanitization policy applied for public repository publication. Given that the system handles highly confidential legal documents, defenses are designed at each layer.

---

## 1. 公開リポジトリのサニタイズ方針

本リポジトリはポートフォリオとして構成を公開するものであり、稼働環境を晒すものではありません。
攻撃対象領域(attack surface)を不必要に拡大しないため、以下を徹底しています。

- **稼働中のフロントエンド URL は掲載しない**: CloudFront ドメイン等の稼働エンドポイントは、
  認証で保護されているとはいえ、公開する積極的理由がなく、攻撃の偵察コストを下げるだけである
  ため、リポジトリには一切記載していません。
- **固有値のプレースホルダ化**: AWS アカウント ID、Cognito User Pool ID / App Client ID、
  Function URL、CloudFront ドメイン / ディストリビューション ID、Knowledge Base ID /
  Data Source ID、アカウント ID を含むバケット名等は、すべて `<PLACEHOLDER>` 形式に
  置換しています。
- **秘匿情報の非コミット**: クレデンシャル、トークン、シークレットは一切コミットしていません
  (`.gitignore` で関連ファイルを除外)。

## 1. Sanitization Policy for the Public Repository

This repository is published to showcase the system's structure as a portfolio and does not expose the live environment. The following measures are strictly applied to avoid unnecessarily expanding the attack surface.

- **Live frontend URL is not included**: Although the live endpoint such as the CloudFront domain is protected by authentication, there is no positive reason to publish it, and doing so would only lower the reconnaissance cost for attackers; therefore, it is not included in the repository at all.
- **Unique values replaced with placeholders**: AWS account ID, Cognito User Pool ID / App Client ID, Function URL, CloudFront domain / distribution ID, Knowledge Base ID / Data Source ID, bucket names containing account IDs, and similar values are all replaced with the `<PLACEHOLDER>` format.
- **No credentials committed**: Credentials, tokens, and secrets are never committed (related files are excluded via `.gitignore`).

---

## 2. 認証・認可

- **トークンベース認証**: Amazon Cognito の Hosted UI による認可コードフロー + PKCE を採用。
  ブラウザはシークレットを保持しない public client であり、PKCE により認可コードの横取りを
  防ぎます。
- **In-Lambda でのトークン検証**: Lambda 内で ID トークンを検証します。検証は厳格化しており、
  署名アルゴリズムを RS256 に固定(alg すり替え防止)、aud(App Client ID)・iss(User Pool)・
  exp・token_use(id)をすべて必須チェックします。これらのいずれかを緩めると認証バイパスに
  直結するため、ライブラリ任せにせず明示的に検証しています。
- **トークン伝送**: Authorization ヘッダ(Bearer)方式。トークンはブラウザのメモリにのみ保持し
  (localStorage 不使用)、XSS によるトークン窃取リスクを緩和します。CSRF は構造的に発生しません。

## 2. Authentication and Authorization

- **Token-based authentication**: The authorization code flow + PKCE via Amazon Cognito's Hosted UI is adopted. The browser uses a public client that does not hold secrets, and PKCE prevents authorization code interception.
- **In-Lambda token validation**: ID tokens are validated within Lambda. Validation is strictly enforced: the signature algorithm is fixed to RS256 (prevents algorithm substitution), and aud (App Client ID), iss (User Pool), exp, and token_use (id) are all mandatory checks. Relaxing any of these would directly enable authentication bypass, so validation is performed explicitly rather than delegated entirely to a library.
- **Token transmission**: Uses the Authorization header (Bearer) method. Tokens are held only in browser memory (no localStorage), mitigating the risk of token theft via XSS. CSRF does not arise structurally.

---

## 3. データ保護とレジデンシ

- **データレジデンシ**: 保存(文書・ベクトル・監査ログ)は東京リージョンに完全固定。推論のみ
  日本国内(東京・大阪)に限定し、Global 推論を IAM レベルで禁止しています。
- **暗号化**: S3 バケットはデフォルト暗号化を適用。監査ログバケットは、カスタマー管理鍵(CMK)
  への切り替えをパラメータで制御可能とし、要件に応じて統制を強化できます。
- **通信の暗号化強制**: バケットポリシーで非 HTTPS 通信を拒否。CloudFront は HTTPS リダイレクトを
  強制します。

## 3. Data Protection and Residency

- **Data residency**: Storage (documents, vectors, audit logs) is fully fixed to the Tokyo region. Inference is limited to within Japan (Tokyo and Osaka), and global inference is prohibited at the IAM level.
- **Encryption**: Default encryption is applied to all S3 buckets. The audit log bucket supports switching to a Customer Managed Key (CMK) via a parameter, allowing enforcement to be strengthened according to requirements.
- **Enforced encrypted communication**: Non-HTTPS communication is denied by bucket policies. CloudFront enforces HTTPS redirects.

---

## 4. 最小権限の原則

- Lambda 実行ロールには、必要な権限のみをリソース限定で付与しています。Bedrock の検索・生成・
  取り込み起動は対象 Knowledge Base に限定、S3 の読み書きは `documents/*`・`audit/*` の
  プレフィックスに限定しています。
- UI ホスティングの S3 バケットは、CloudFront(OAC)経由でのみアクセス可能とし、直接の
  パブリックアクセスを全面的にブロックしています。S3 直 URL は 403、CloudFront 経由のみ 200
  となる構成を確認しています。

## 4. Principle of Least Privilege

- The Lambda execution role is granted only the necessary permissions, scoped to specific resources. Bedrock retrieval, generation, and ingestion job triggers are restricted to the target Knowledge Base; S3 read/write is restricted to the `documents/*` and `audit/*` prefixes.
- The S3 bucket for UI hosting is accessible only via CloudFront (OAC), with direct public access fully blocked. The configuration is verified so that direct S3 URLs return 403 and only CloudFront-routed access returns 200.

---

## 5. 監査可能性

- 全リクエストについて、利用者 ID(不変の sub)・質問・回答・引用・タイムスタンプ・リクエスト ID
  を専用 S3 バケットに記録します。
- 監査ログバケットはバージョニングを有効化し、改ざん耐性を持たせています。
- 監査ログにはライフサイクル(一定期間後の低頻度アクセス階層への移行、長期での削除)を設定し、
  保持とコストを両立しています。

## 5. Auditability

- For every request, the user ID (immutable sub), question, answer, citations, timestamp, and request ID are recorded in a dedicated S3 bucket.
- The audit log bucket has versioning enabled to provide tamper resistance.
- A lifecycle policy is configured for the audit log bucket (transition to infrequent-access tier after a certain period, deletion after long-term retention) to balance retention requirements with cost.

---

## 6. PII の取り扱い

- 文書取り込み前に、定型パターンの個人情報(メールアドレス・電話番号・カード番号)を
  マスキングします。検索に必要な情報(当事者名・住所・金額・管轄等)は、秘匿性と検索実用性の
  衡量に基づき保持しています。
- より高い秘匿性が要求される用途では、自由記述 PII を対象とする生成 AI ベースのマスキングや
  カスタム辞書方式への拡張余地があります(設計上の選択肢として記録)。

## 6. Handling of PII

- Before document ingestion, structured-pattern personally identifiable information (email addresses, phone numbers, card numbers) is masked. Information necessary for search (party names, addresses, amounts, jurisdiction, etc.) is retained based on a trade-off between confidentiality and search utility.
- For use cases requiring higher confidentiality, there is room to extend to generative AI-based masking targeting free-form PII, or a custom dictionary approach (recorded as a design option).

---

## 7. 脆弱性の報告

本リポジトリは閲覧目的の公開であり、稼働環境を含みません。構成上の助言等があれば、
リポジトリのオーナーへご連絡ください。

## 7. Vulnerability Reporting

This repository is published for viewing purposes only and does not include a live environment. If you have any advice regarding the configuration, please contact the repository owner.
