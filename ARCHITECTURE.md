# アーキテクチャ

本ドキュメントは、システム構成・データフロー・データレジデンシ設計を図示します。
図は GitHub 上でネイティブにレンダリングされる Mermaid 記法で記述しています。

> 固有値(アカウント ID・エンドポイント URL・リソース ID 等)はすべてプレースホルダ化しています。

---

## 1. システム全体構成

```mermaid
flowchart TB
    subgraph Client["ブラウザ (利用者)"]
        UI["単一ファイル SPA<br/>index.html"]
    end

    subgraph Edge["配信層"]
        CF["CloudFront + OAC"]
    end

    subgraph Auth["認証層 (Amazon Cognito)"]
        HUI["Hosted UI<br/>(認可コード + PKCE)"]
        UP["User Pool +<br/>public client"]
    end

    subgraph App["アプリケーション層"]
        FU["Lambda Function URL<br/>(CORS / AuthType: NONE)"]
        L["Lambda (Python 3.13)<br/>認証検証・RAG・<br/>アップロード・マスキング・監査"]
        LAYER["PyJWT レイヤー"]
    end

    subgraph RAG["RAG 層 (Amazon Bedrock)"]
        KB["Knowledge Base"]
        EMB["Titan Embeddings V2"]
        VEC["S3 Vectors"]
        GEN["Claude Sonnet<br/>(日本国内推論)"]
    end

    subgraph Storage["ストレージ層 (S3, 東京固定)"]
        DOCB["文書バケット<br/>UI + documents/"]
        AUDB["監査ログバケット<br/>暗号化/バージョニング/<br/>ライフサイクル"]
    end

    UI -->|"1. ログイン"| HUI
    HUI --- UP
    UI -->|"2. 静的配信"| CF
    CF -->|"OAC"| DOCB
    UI -->|"3. Bearer トークン"| FU
    FU --> L
    L --- LAYER
    L -->|"トークン検証"| UP
    L -->|"検索 + 生成"| KB
    KB --- EMB
    KB --- VEC
    KB --- GEN
    KB -.->|"参照"| DOCB
    L -->|"監査記録"| AUDB
    L -->|"presigned URL 発行"| DOCB
```

---

## 2. 質問処理のシーケンス

```mermaid
sequenceDiagram
    participant U as ブラウザ
    participant C as Cognito (Hosted UI)
    participant L as Lambda
    participant B as Bedrock KB
    participant A as 監査ログ (S3)

    U->>C: ログイン (認可コード + PKCE)
    C-->>U: ID トークン
    U->>L: 質問 + Authorization: Bearer
    L->>L: ID トークン検証<br/>(RS256/aud/iss/exp/token_use)
    L->>L: sub で利用者確定
    L->>B: RetrieveAndGenerate
    B-->>L: 回答 + 引用
    L->>A: 監査記録<br/>(利用者/質問/回答/引用/時刻)
    L-->>U: 引用付き回答
```

---

## 3. アップロードと自動取り込み・PIIマスキングのシーケンス

```mermaid
sequenceDiagram
    participant U as ブラウザ
    participant L as Lambda
    participant S as S3 (documents/)
    participant B as Bedrock KB

    U->>L: アップロード要求 (Bearer)
    L->>L: トークン検証
    L-->>U: presigned URL (PUT)
    U->>S: ファイルを直接 PUT
    S->>L: S3 イベント (ObjectCreated)
    alt 未マスキング (メタデータ無し)
        L->>S: 文書を取得 (GetObject)
        L->>L: 正規表現で PII マスキング<br/>(メール/電話/カード)
        L->>S: マスキング済みで書き戻し<br/>(メタデータ pii-masked=true)
        Note over L,S: 書き戻しが再度イベントを発火
        L->>B: 取り込みジョブ起動
    else マスキング済み (メタデータ有り)
        Note over L: マスキングをスキップ<br/>(ループは1回で収束)
        L->>B: 取り込みジョブ起動
    end
    B-->>B: ベクトル化・インデックス
```

---

## 4. データレジデンシの非対称設計

```mermaid
flowchart LR
    subgraph Tokyo["東京リージョン (保存=完全固定)"]
        S3D["文書 / ベクトル / 監査ログ"]
    end

    subgraph JP["日本国内 (推論のみ)"]
        T["東京"]
        O["大阪"]
    end

    GLOBAL["Global 推論<br/>(IAM で禁止)"]

    S3D -->|"保存は東京固定"| S3D
    S3D -.->|"推論リクエスト"| T
    S3D -.->|"推論リクエスト"| O
    S3D -. "禁止" .-x GLOBAL

    style GLOBAL stroke-dasharray: 5 5,color:#999
    style Tokyo fill:#eef2f3
    style JP fill:#eef7ee
```

保存(文書・ベクトル・監査ログ)は東京リージョンに完全固定する一方、生成モデルが東京
In-Region 非対応のため、推論のみ日本国内(東京・大阪)に限定するクロスリージョン推論
プロファイルを用います。Global 推論は IAM レベルで禁止しています。

---

## 5. IaC のスタック構成

小規模かつリソースが密結合(文書バケットが CloudFront・Lambda・イベント通知と多方向に結合)
であるため、単一スタックに集約しています。スタックを分割すると、OAC のバケットポリシーや
S3 イベント通知が双方向参照を生み、循環参照が発生するためです。

```mermaid
flowchart TB
    subgraph RagStack["RagStack (単一スタック)"]
        direction TB
        B1["S3: 監査ログ / 文書"]
        C1["Cognito: User Pool / client / domain"]
        D1["CloudFront + OAC"]
        L1["Lambda + Function URL + IAM"]
        N1["S3 イベント通知 (documents/ → Lambda)"]
    end

    KBExt["Knowledge Base<br/>(既存 ID を参照・手動構築)"]

    L1 -.->|"ID 参照"| KBExt
    B1 --- D1
    B1 --- N1
    N1 --- L1
    D1 --- B1
```

Knowledge Base は L2 コンストラクト非対応かつ S3 Vectors との結合が複雑で、IaC 完全再現の
費用対効果が低いため、既存リソースを ID 参照する形にとどめ、構築手順は別途文書化しています。
