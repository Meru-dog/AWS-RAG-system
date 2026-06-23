# プロジェクト総括レポート:法務文書RAGアプリケーションの設計・実装・運用整備

**プロジェクト**: AWS サーバーレス RAG アプリケーション(法務文書対象)
**準拠文書**: RDD v1.2(第12章 実装ロードマップ S0〜S5)
**到達状態**: 全フェーズ完了
**本書の位置づけ**: 各フェーズ完了レポート(S1〜S5・S3)の上位に立つ全体総括

# Project Summary Report: Design, Implementation, and Operations of a Legal Document RAG Application

**Project**: AWS Serverless RAG Application (targeting legal documents)
**Reference Document**: RDD v1.2 (Chapter 12: Implementation Roadmap S0–S5)
**Completion Status**: All phases completed
**Purpose of This Document**: Overall summary superseding individual phase completion reports (S1–S5 and S3)

---

## 第1部 概観

### 1.1 プロジェクトの目的と成果

本プロジェクトの目的は、機密性を要する法務文書(契約書)を対象に、月額5,000円未満・
低設計負荷という制約下で、実運用に耐えるRAG(Retrieval-Augmented Generation)
アプリケーションを構築することにあった。

開始時点では、Bedrock Knowledge Base への問い合わせと引用付き回答という中核機能は
動作していたものの、認証は脆弱で、利用者識別も監査もなく、文書取り込みは手動であり、
品質を測定する手段もなく、構成はすべて手作業で再現性を欠いていた。

完了時点では、Cognito による認証、利用者単位の監査ログ、UIからのアップロードと自動取り込み、
PIIマスキング、マネージド評価による品質測定基盤、そして AWS CDK による再現可能な
Infrastructure as Code を備えた、運用に耐えるシステムへと到達した。

### 1.2 設計思想の優先順位

全フェーズを貫く設計指針は、明示された優先順位に従った:
**コスト最小化 → 設計負荷最小化 → 機密性 → 品質 → レイテンシ**。

この優先順位は、単なるスローガンではなく、個別の技術選択を実際に決定づけた。例えば、
ベクトルストアに S3 Vectors を採用し OpenSearch Serverless を退けたのはアイドル課金の
回避(コスト)であり、認証検証を In-Lambda の軽量な方式としたのは設計負荷最小化であり、
reranker を測定の上で見送ったのは品質と簡潔性の衡量であった。優先順位が明示されていた
ことで、トレードオフに直面するたびに一貫した基準で判断できた。

## Part 1: Overview

### 1.1 Project Objectives and Outcomes

The objective of this project was to build a RAG (Retrieval-Augmented Generation) application capable of production use, targeting confidential legal documents (contracts), under the constraints of under ¥5,000/month and low design overhead.

At the start, the core functionality of querying Bedrock Knowledge Base and returning answers with citations was operational, but authentication was weak, there was no user identification or auditing, document ingestion was manual, there were no means to measure quality, and the entire configuration was manual with no reproducibility.

At completion, the system reached production-readiness with Cognito-based authentication, per-user audit logging, UI-based upload with automated ingestion, PII masking, a quality measurement infrastructure via managed evaluation, and reproducible Infrastructure as Code using AWS CDK.

### 1.2 Design Philosophy and Priorities

The design principle guiding all phases followed an explicitly stated priority order:
**Cost minimization → Design overhead minimization → Confidentiality → Quality → Latency**.

This priority order was not merely a slogan — it actually determined individual technology choices. For example, adopting S3 Vectors as the vector store over OpenSearch Serverless was to avoid idle billing (cost); using a lightweight In-Lambda approach for authentication validation was to minimize design overhead; and rejecting the reranker after measurement was a trade-off between quality and simplicity. Having the priority order explicitly stated allowed consistent decision-making each time a trade-off arose.

---

## 第2部 アーキテクチャ

### 2.1 全体構成

完成したシステムの構成要素と関係は以下のとおりである。

**データ層**
- 文書バケット(rag-document):UI(ルート)と文書(documents/ プレフィックス)を同居。
  ベクトル化対象は documents/ 配下のみ。
- 監査ログバケット(rag-audit-log-…):暗号化・バージョニング・ライフサイクルを備える。
- S3 Vectors:ベクトルストア。アイドル課金回避のため採用。
- Titan Text Embeddings V2:埋め込み。

**RAG層**
- Bedrock Knowledge Base(rag-document-v2):マネージドRAG。
- 生成モデル:Claude Sonnet 4.6(JP CRIS 推論プロファイル。推論を日本国内に限定)。

**アプリケーション層**
- Lambda(rag-document, Python 3.13):認証検証・質問処理・アップロードURL発行・
  PIIマスキング・取り込み起動・監査記録を担う単一関数。
- Lambda Function URL(AuthType: NONE、アプリ層でトークン検証):API エンドポイント。
- PyJWT レイヤー:Cognito ID トークン検証用。

**認証・配信層**
- Cognito User Pool + public client(PKCE):Hosted UI による認可コードフロー。
- CloudFront + OAC:UI を非公開 S3 から配信。

### 2.2 リクエストのライフサイクル

**質問**:ブラウザが Hosted UI でログイン(PKCE)→ ID トークン取得 → Function URL へ
Bearer 送出 → Lambda が ID トークン検証(RS256/aud/iss/exp/token_use)→ sub で利用者確定 →
Bedrock KB が検索・生成 → 引用付き回答を返却 → 監査ログを S3 に記録。

**アップロード**:ブラウザが Lambda に presigned URL を要求 → S3 へ直接 PUT(documents/)→
S3 イベントで Lambda 起動 → PII マスキング(正規表現)→ メタデータ付きで書き戻し →
取り込みジョブ起動 → 数分後に検索対象として反映。

### 2.3 データレジデンシの非対称設計

v1.2 で確定した重要制約として、保存と推論で地理的扱いが異なる。保存は東京 In-Region に
完全固定する一方、Sonnet 4.6 が東京 In-Region 非対応のため、推論のみ JP CRIS(東京・大阪)
に限定し、Global CRIS は IAM レベルで禁止した。「保存=東京固定/推論=日本国内」という
非対称性を、全フェーズで一貫して維持した。

## Part 2: Architecture

### 2.1 Overall Configuration

The components and relationships of the completed system are as follows.

**Data Layer**
- Document bucket (rag-document): UI (root) and documents (documents/ prefix) coexist in the same bucket. Only content under documents/ is subject to vectorization.
- Audit log bucket (rag-audit-log-…): Equipped with encryption, versioning, and lifecycle policies.
- S3 Vectors: Vector store. Adopted to avoid idle billing.
- Titan Text Embeddings V2: Embedding model.

**RAG Layer**
- Bedrock Knowledge Base (rag-document-v2): Managed RAG.
- Generative model: Claude Sonnet 4.6 (JP CRIS inference profile; inference restricted to within Japan).

**Application Layer**
- Lambda (rag-document, Python 3.13): Single function responsible for authentication validation, query processing, presigned URL issuance, PII masking, ingestion trigger, and audit recording.
- Lambda Function URL (AuthType: NONE, token validation at application layer): API endpoint.
- PyJWT layer: For Cognito ID token validation.

**Authentication and Delivery Layer**
- Cognito User Pool + public client (PKCE): Authorization code flow via Hosted UI.
- CloudFront + OAC: Serves UI from a private S3 bucket.

### 2.2 Request Lifecycle

**Query**: Browser logs in via Hosted UI (PKCE) → Obtains ID token → Sends Bearer to Function URL → Lambda validates ID token (RS256/aud/iss/exp/token_use) → Identifies user by sub → Bedrock KB retrieves and generates → Returns response with citations → Records audit log to S3.

**Upload**: Browser requests presigned URL from Lambda → Direct PUT to S3 (documents/) → Lambda triggered by S3 event → PII masking (regex) → Write back with metadata → Trigger ingestion job → Available for search after a few minutes.

### 2.3 Asymmetric Data Residency Design

As a critical constraint finalized in v1.2, storage and inference are treated differently geographically. Storage is fully fixed to Tokyo In-Region, while Sonnet 4.6 does not support Tokyo In-Region inference, so inference is limited to JP CRIS (Tokyo and Osaka), and Global CRIS is prohibited at the IAM level. The asymmetry of "storage = Tokyo fixed / inference = within Japan" was consistently maintained throughout all phases.

---

## 第3部 フェーズ別の実装過程

### S0:基盤是正

データレジデンシの是正、API キー残骸の撤去方針、AWS Budgets による予算統制を整えた。
以降の全フェーズの前提となる土台である。

### S1:認証正常化・利用者識別・監査ログ・UI

認証を「動くが認証のないプロトタイプ」から「認証付きで安全に使えるアプリ」へ引き上げた。
API キー方式を撤去し、Cognito ID トークン検証ゲート(In-Lambda、東京)に置換。検証は
RS256 固定・aud/iss/exp/token_use の厳格化により認証バイパスを防いだ。利用者IDは不変の
sub を正準キーとし、全リクエストを S3 に監査記録(FR-09)。UI は依存ライブラリゼロの単一
index.html として、Hosted UI + PKCE のログインと Function URL 呼び出しを実装。UIホスティングは
CloudFront + OAC により非公開化した。

このフェーズで、「SigV4 を撤去する」という RDD の記述が、実際には SigV4 が未設定であった
ため死文であることが判明し、スコープが「撤去+新設」から「新設のみ」へ単純化された。

### S2:自動取り込みとUIアップロード

文書取り込みを手動から自動パイプラインへ移行した。presigned URL によりブラウザから S3 へ
直接アップロードし、S3 イベントで取り込みを自動起動する構成とした。前提として、UIファイルと
文書を documents/ プレフィックスで空間分離し、Knowledge Base の参照範囲も documents/ に
限定して、UIファイルの誤取り込みを防いだ。実装過程で、CORS ヘッダの二重付与と presigned URL
の署名不一致(リージョンエンドポイント未指定)という2つの問題を解決した。

### S3:PIIマスキング

取り込み前に、正規表現ベースの限定マスキングを実装した。当初 RDD が想定した Comprehend は
PII 検出が日本語非対応であることが判明し、この制約と「限定マスキング」という選択が、
外部依存も言語制約もない正規表現方式へ自然に導いた。メール・電話・カード番号を秘匿しつつ、
当事者名・住所・金額・管轄は検索のため保持。無限ループは、書き戻し時のメタデータ
pii-masked=true により1回で収束させた。実装過程で、GetObject 権限欠如・無効パラメータ・
取り込み同時実行という3障害を、ログに基づき順に解決した。

### S4:評価駆動の品質改善

Bedrock RAG 評価(マネージドの LLM-as-a-judge)による評価基盤を構築した。弁護士の観点で
評価データセット(本体20問・忠実性特化8問、5カテゴリ)を設計し、ベースライン品質を測定
(正確性0.88・忠実性0.93等)。改善策として reranker を導入し定量比較したところ、忠実性・
引用精度は改善する一方で網羅性が大きく低下するトレードオフが判明した。横断比較質問で必要な
複数文書を絞り込みすぎることが原因と個別分析で特定し、法務RAGにおける網羅性の重要性から
reranker を採用しない意思決定を下した。測定により品質低下を未然に回避した、評価駆動の成功例
である。

### S5:運用整備とIaC

手作業で積み上げた構成を AWS CDK(Python)でコード化し、再現可能性を確立した。稼働中の
本番に手を入れず現行構成を正確に記述する方針(A方針)を採用。スタック構成は、5分割案で
循環参照に直面し、2分割でも S3 イベント通知が再び循環を生んだことから、小規模・密結合という
実態に即して単一スタックへ収束させた。運用整備として、監査ログのライフサイクル(90日IA移行・
3年削除)、CMK のパラメータ切替(既定 SSE-S3)を組み込み、繰り越した一時設定(旧クライアント
等)を後始末した。再現手順書により、CDKが自動再現する範囲と手動範囲を明示した。

## Part 3: Implementation Process by Phase

### S0: Foundation Correction

Data residency was corrected, a policy for removing API key remnants was established, and budget controls via AWS Budgets were put in place. This formed the prerequisite foundation for all subsequent phases.

### S1: Authentication Normalization, User Identification, Audit Logging, and UI

Authentication was elevated from a "working but unauthenticated prototype" to a "safely usable application with authentication." The API key method was removed and replaced with a Cognito ID token validation gate (In-Lambda, Tokyo). Validation prevented authentication bypass by fixing RS256 and enforcing strict aud/iss/exp/token_use checks. The immutable sub was used as the canonical user ID key, and all requests were recorded to S3 as audit logs (FR-09). The UI was implemented as a single index.html with zero dependency libraries, handling Hosted UI + PKCE login and Function URL calls. UI hosting was made private via CloudFront + OAC.

In this phase, it was discovered that the RDD's description of "removing SigV4" was a dead letter because SigV4 was not configured in the first place, simplifying the scope from "removal + new setup" to "new setup only."

### S2: Automated Ingestion and UI Upload

Document ingestion was migrated from manual to an automated pipeline. Direct upload from the browser to S3 via presigned URL was implemented, with ingestion automatically triggered by S3 events. As a prerequisite, UI files and documents were spatially separated using the documents/ prefix, and the Knowledge Base's reference scope was also limited to documents/, preventing accidental ingestion of UI files. During implementation, two issues were resolved: duplicate CORS header attachment and presigned URL signature mismatch (caused by unspecified region endpoint).

### S3: PII Masking

Limited regex-based masking was implemented before ingestion. It was discovered that Amazon Comprehend — which the RDD originally assumed — did not support Japanese PII detection, and this constraint combined with the "limited masking" policy naturally led to a regex-based approach with no external dependencies or language constraints. Email addresses, phone numbers, and card numbers are masked, while party names, addresses, amounts, and jurisdiction are retained for search purposes. Infinite loops were prevented by using the pii-masked=true metadata on write-back, ensuring the loop terminates in one pass. During implementation, three failures were resolved in sequence based on logs: missing GetObject permission, invalid parameters, and concurrent ingestion execution.

### S4: Evaluation-Driven Quality Improvement

A quality measurement infrastructure was built using Bedrock RAG evaluation (managed LLM-as-a-judge). An evaluation dataset was designed from a lawyer's perspective (20 main questions and 8 fidelity-focused questions across 5 categories), and baseline quality was measured (accuracy 0.88, fidelity 0.93, etc.). A reranker was introduced as an improvement measure and compared quantitatively, revealing a trade-off: while fidelity and citation accuracy improved, recall dropped significantly. Individual analysis identified that cross-contract comparison queries suffered from over-filtering of necessary documents, and a decision was made not to adopt the reranker given the importance of recall in legal RAG. This is a successful example of evaluation-driven decision-making that preemptively avoided quality degradation.

### S5: Operations Setup and IaC

The configuration built up manually was codified in AWS CDK (Python), establishing reproducibility. A policy (Policy A) was adopted of accurately describing the current configuration without touching the running production environment. The stack configuration encountered circular references with the 5-stack split plan, and even a 2-stack split caused S3 event notifications to create circular references again; therefore, it was converged to a single stack to match the reality of small scale and tight coupling. For operations, audit log lifecycle policies (90-day IA transition, 3-year deletion), CMK parameter switching (default SSE-S3), and cleanup of carried-over temporary settings (old clients, etc.) were incorporated. A reproduction procedure document explicitly clarified what CDK automates and what must be done manually.

---

## 第4部 横断的に現れた意思決定の型

本プロジェクトの実装は、フェーズを越えて反復された幾つかの意思決定パターンに支えられている。
これらは、個別の技術判断を超えた、プロジェクトの方法論的特徴である。

### 4.1 制約を起点とした設計の単純化

繰り返し現れたのは、制約が判明したときに、それを迂回する複雑な手段を採るのではなく、制約と
既存の方針を組み合わせてより単純な解へ収束させる動きである。Comprehend の日本語非対応は、
限定マスキングという選択と組み合わさって正規表現方式という最も簡潔な解を導いた。スタックの
循環参照は、密結合という実態と組み合わさって単一スタックという定石へ導いた。いずれも、制約を
「障害」ではなく「設計を正しい方向へ絞り込む情報」として扱った。

### 4.2 測定に基づく意思決定

reranker の検証が典型である。「rerankerを入れれば良くなる」という素朴な期待を所与とせず、
ベースラインを測定し、reranker 適用後と定量比較し、トレードオフを個別質問レベルで分析した
上で、採用しない判断を下した。勘や通念ではなく測定が意思決定を駆動した。これは品質フェーズ
固有の話ではなく、ログ・メトリクスによる障害切り分け(S3)にも同じ姿勢が貫かれている。

### 4.3 誤った前提の発見と訂正

RDD の記述が現実と食い違う場面が複数あった。SigV4 が未設定であったこと(S1)、Comprehend が
日本語非対応であること(S3)、Lambda 実行ロールに想定した権限が欠けていたこと(S3)。これらは
いずれも、計画を盲目的に実行するのではなく、実環境の事実を確認することで発見された。誤った
前提に基づく死文を訂正し、現実に即して再設計する作業が、各所で品質を担保した。

### 4.4 簡潔性と堅牢性の衡量

「過剰設計を避ける」(RDD §11)という方針が、随所で具体的判断に翻訳された。検証を In-Lambda で
行いゲートウェイ階層を増やさない、トークン伝送を Bearer とし CSRF 機構を避ける、監査ログを
1リクエスト1オブジェクトとし追記の複雑さを排する、スタックを単一にまとめる。いずれも、規模に
見合った最小構成を選び、必要な堅牢性(厳格なトークン検証、改ざん耐性、最小権限)は妥協しない、
という衡量の産物である。

## Part 4: Cross-Cutting Decision-Making Patterns

The implementation of this project was supported by several decision-making patterns that recurred across phases. These represent methodological characteristics of the project that transcend individual technical judgments.

### 4.1 Design Simplification Starting from Constraints

A recurring pattern was, when a constraint was discovered, not taking a complex workaround but instead combining the constraint with existing policies to converge on a simpler solution. Comprehend's lack of Japanese support, combined with the choice of limited masking, led to the most concise solution: the regex approach. Stack circular references, combined with the reality of tight coupling, led to the established approach of a single stack. In both cases, constraints were treated not as "obstacles" but as "information that narrows the design toward the right direction."

### 4.2 Measurement-Based Decision-Making

The reranker evaluation is a prime example. Rather than taking for granted the naive expectation that "adding a reranker will make things better," the baseline was measured, the result after reranker application was quantitatively compared, and the trade-off was analyzed at the individual question level before making the decision not to adopt it. Measurement, not intuition or conventional wisdom, drove the decision. This is not unique to the quality phase — the same stance is applied to failure isolation using logs and metrics (S3).

### 4.3 Discovery and Correction of Incorrect Assumptions

There were multiple instances where the RDD's descriptions conflicted with reality: SigV4 was not configured (S1), Comprehend does not support Japanese (S3), and the Lambda execution role lacked the expected permissions (S3). All of these were discovered not by blindly executing the plan, but by verifying the facts in the actual environment. Correcting dead letters based on incorrect assumptions and redesigning to match reality ensured quality at each stage.

### 4.4 Balancing Simplicity and Robustness

The policy of "avoiding over-engineering" (RDD §11) was translated into concrete decisions throughout. Performing validation In-Lambda to avoid adding gateway layers; using Bearer for token transmission to avoid CSRF mechanisms; using one object per request for audit logs to eliminate append complexity; consolidating to a single stack. All of these are products of choosing the minimum configuration appropriate to the scale while not compromising on necessary robustness (strict token validation, tamper resistance, least privilege).

---

## 第5部 制度・システム設計上の含意

法務文書を扱う RAG システムには、一般的な RAG とは異なる固有の論点がある。本プロジェクトは
それらに対し、いくつかの設計上の応答を与えた。

### 5.1 忠実性と網羅性の優位

法務文書では、「書かれていないことを書かない」忠実性が信頼性の核心である。評価データセットは
この観点を中心に設計され(忠実性特化セット)、ベースラインの忠実性0.93を確認した。同時に、
複数契約の横断比較における網羅性が、reranker 不採用の決定打となった。一般的な RAG では
許容される「関連度上位への絞り込み」が、契約比較では一部の契約を脱落させ、実務判断を誤らせる。
法務という領域の要請が、技術選択を規定した好例である。

### 5.2 秘匿性と検索可能性の緊張

PII マスキングは、秘匿すべき情報と検索に必要な情報がしばしば同一の対象(当事者名)であるという
法務固有の緊張を顕在化させた。限定マスキングは、この緊張に対し「定型的な機微情報は秘匿し、
当事者の同定に関わる情報は検索のため保持する」という割り切りを与えた。これは絶対的な正解では
なく、用途に応じた制度設計上の選択であり、より高い秘匿性が要求される場面では別の均衡点が要る。

### 5.3 監査可能性の作り込み

誰が・いつ・何を尋ね・どう答えたかを記録する監査ログは、法務業務の説明責任に直結する。
これをフェイルオープン(監査の一時不具合で回答を止めない)としたのは「機能 > 運用」の優先順位
に基づく判断だが、より厳格な統制が求められる場面ではフェイルクローズへの切替余地を残した。
監査の完全性と利用可能性のいずれを優先するかも、制度設計上の選択である。

## Part 5: Institutional and System Design Implications

A RAG system handling legal documents has unique considerations that differ from general RAG. This project provided several design responses to those considerations.

### 5.1 Primacy of Fidelity and Recall

In legal documents, fidelity — "not writing what is not written" — is the core of reliability. The evaluation dataset was designed around this perspective (fidelity-focused set), confirming a baseline fidelity of 0.93. At the same time, recall in cross-contract comparisons was the decisive factor in rejecting the reranker. "Filtering to the top by relevance," which is acceptable in general RAG, causes some contracts to drop out in contract comparison, leading to erroneous practical judgments. This is a good example of the domain requirements of law governing technology choices.

### 5.2 Tension Between Confidentiality and Searchability

PII masking surfaced a tension unique to legal contexts: the information that should be kept secret and the information needed for search are often the same (party names). Limited masking provided a resolution to this tension: "mask structured sensitive information, but retain information related to party identification for search." This is not an absolute correct answer but a design choice appropriate to the use case; a different equilibrium point is required when higher confidentiality is demanded.

### 5.3 Built-in Auditability

Audit logs recording who asked what, when, and how it was answered are directly tied to accountability in legal work. Making this fail-open (not stopping responses due to a temporary auditing malfunction) was a judgment based on the priority of "function > operation," but room was left to switch to fail-close when stricter controls are required. Whether to prioritize audit completeness or availability is also a design choice.

---

## 第6部 残された課題と発展方向

| 領域 | 課題 | 想定される対応 |
|---|---|---|
| 取り込み | OCR 未対応(テキスト系のみ) | 画像PDF対応時に BDA 等のテキスト抽出層を追加 |
| マスキング | 自由記述PII(氏名・住所)は非マスキング | 高秘匿用途では生成AI/カスタム辞書方式へ拡張 |
| 取り込み | ジョブ同時実行の ConflictException | 書き戻し時の取り込み起動抑制など重複制御 |
| IaC | Knowledge Base は手動構築(ID参照) | L2対応進展時にコード化を再検討 |
| IaC | synth 止まり(deploy 未実施・A方針) | 再現必要時に手順書に従い deploy(名前衝突回避) |
| 品質 | 忠実性特化セットの測定が未実施 | 同一評価者でハルシネーション耐性を別途測定 |
| 品質 | 契約取り違え(検索候補段階の弱点) | チャンキング戦略・取得数・embedding の見直し |
| 構成 | UI/文書バケットの同居 | 配信用と格納用の分離(リファクタ) |
| 運用 | 監査ログのフェイルクローズ化・CMK本適用 | 統制要件の高まりに応じて切替 |

これらはいずれも、現状の到達点を損なうものではなく、要件の変化や規模拡大に応じて段階的に
対応すべき発展方向である。各課題には、対応の処方が既に特定されている。

## Part 6: Remaining Challenges and Future Directions

| Area | Challenge | Anticipated Response |
|---|---|---|
| Ingestion | No OCR support (text-based only) | Add a text extraction layer such as BDA when image PDF support is needed |
| Masking | Free-form PII (names, addresses) is not masked | Extend to generative AI or custom dictionary approach for high-confidentiality use cases |
| Ingestion | ConflictException on concurrent job execution | Duplicate control such as suppressing ingestion trigger on write-back |
| IaC | Knowledge Base is manually constructed (ID reference) | Reconsider codification when L2 construct support advances |
| IaC | Stopped at synth (deploy not executed; Policy A) | Follow the reproduction procedure to deploy when needed (avoiding name conflicts) |
| Quality | Fidelity-focused set measurement not yet executed | Measure hallucination resistance separately with the same evaluator |
| Quality | Contract confusion (weakness at the retrieval candidate stage) | Revisit chunking strategy, retrieval count, and embedding |
| Configuration | UI and document bucket coexistence | Separate delivery and storage buckets (refactor) |
| Operations | Fail-close audit logging and full CMK application | Switch in response to increasing control requirements |

None of these compromise the current state of achievement; they are all future directions to be addressed incrementally as requirements change or the system scales. A prescribed response has already been identified for each challenge.

---

## 第7部 結語

本プロジェクトは、「簡単な UI からシンプルな AWS サービスで RAG を構築する」という出発点から、
認証・監査・自動取り込み・PIIマスキング・品質評価基盤・再現可能な IaC を備えた、運用に耐える
法務文書 RAG システムへと到達した。

その過程は、明示された優先順位に従い、制約を設計の指針として扱い、測定と事実確認によって
意思決定を駆動し、簡潔性と堅牢性を衡量し続ける営みであった。各フェーズで直面した制約
(Comprehend の言語制約、reranker のトレードオフ、リソースの密結合、誤った前提)は、いずれも
論拠に基づく判断によって、システムをより正しい方向へ収束させる契機となった。

完成したシステムは、技術的な動作の達成にとどまらず、法務という領域固有の要請(忠実性の優位、
秘匿性と検索可能性の緊張、監査可能性)に対する設計上の応答を内包している。そして、その全体が
コードと文書によって再現可能な形で記録された。これは、単一のアプリケーションの完成であると
同時に、制度的・システム的な設計判断の集積としての価値を持つ。

## Part 7: Conclusion

This project advanced from the starting point of "building RAG from a simple UI using straightforward AWS services" to a production-ready legal document RAG system equipped with authentication, auditing, automated ingestion, PII masking, a quality evaluation infrastructure, and reproducible IaC.

The process was one of following explicitly stated priorities, treating constraints as design guidelines, driving decisions through measurement and fact verification, and continuously balancing simplicity and robustness. The constraints encountered in each phase (Comprehend's language limitation, the reranker trade-off, tight resource coupling, incorrect assumptions) each became an opportunity to converge the system toward a more correct direction through reasoned judgment.

The completed system not only achieves technical operation but also embodies design responses to the unique requirements of the legal domain (primacy of fidelity, the tension between confidentiality and searchability, auditability). And the entirety of this is recorded in a reproducible form through code and documentation. This holds value not only as the completion of a single application, but also as an accumulation of institutional and systemic design decisions.
