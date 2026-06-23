import json
import os
import re
import uuid
import datetime
import boto3
from botocore.config import Config
import jwt
from jwt import PyJWKClient

# --- Bedrock / Knowledge Base ---
REGION = "ap-northeast-1"
bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]
DATA_SOURCE_ID = os.environ["DATA_SOURCE_ID"]
MODEL_ARN = os.environ["MODEL_ARN"]

# --- Cognito 認証(S1-2)---
USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
APP_CLIENT_ID = os.environ["COGNITO_APP_CLIENT_ID"]
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"
_jwks_client = PyJWKClient(f"{ISSUER}/.well-known/jwks.json")

# --- S3 / 監査ログ / アップロード ---
s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=f"https://s3.{REGION}.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
AUDIT_BUCKET = os.environ["AUDIT_BUCKET"]
DOCUMENT_BUCKET = os.environ["DOCUMENT_BUCKET"]   # 例: rag-document
DOCUMENT_PREFIX = "documents/"

# --- CORS ---
# CORS は Lambda Function URL の CORS 設定に一本化する(コード側では付与しない)。
# コードからもヘッダを足すと Access-Control-Allow-Origin が二重になり、ブラウザが拒否するため。

# アップロード許可拡張子(A方針:テキスト/通常PDF)
ALLOWED_EXTS = {".txt", ".pdf", ".md", ".csv", ".html", ".doc", ".docx"}


# --- PII マスキング(S3:正規表現ベースの限定マスキング)---
# 検索に不要で明らかに秘匿すべき定型パターンに限定する。
# 当事者名・住所・組織名・金額・日付・条番号は検索に必要なため対象にしない。
_PII_PATTERNS = [
    # メールアドレス
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    # クレジット/デビットカード番号(4桁-4桁-4桁-4桁、区切りは - 空白 なし)
    (re.compile(r"\b(?:\d[ -]?){13,16}\b(?<=\d)"), "[CARD]"),
    # 日本の電話番号(0始まり、ハイフン/括弧区切りを含む一般形)
    (re.compile(r"0\d{1,4}[-(]?\d{1,4}[-)]?\d{3,4}"), "[PHONE]"),
]


def _mask_pii(text):
    """限定マスキング:メール・カード・電話の定型パターンを伏字化して返す。"""
    masked = text
    for pattern, repl in _PII_PATTERNS:
        masked = pattern.sub(repl, masked)
    return masked


class AuthError(Exception):
    """有効な Cognito ID トークンを持たないリクエストで送出(→ 401)。"""


def _verify_id_token(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise AuthError("missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, signing_key.key,
            algorithms=["RS256"], audience=APP_CLIENT_ID, issuer=ISSUER, leeway=5,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e
    if claims.get("token_use") != "id":
        raise AuthError("not an id token")
    return claims


def _write_audit_log(user_id, question, answer, citation_uris, request_id):
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "timestamp": now.isoformat(), "user_id": user_id,
        "question": question, "answer": answer,
        "citation_uris": citation_uris, "request_id": request_id,
    }
    key = f"audit/{now:%Y/%m/%d}/{now:%H%M%S}_{request_id}.json"
    try:
        s3.put_object(
            Bucket=AUDIT_BUCKET, Key=key,
            Body=json.dumps(record, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as e:
        print(f"audit log write failed: {e}")  # フェイルオープン


# マスキング対象とするテキスト系拡張子(バイナリは安全にスキップして取り込みのみ)
_MASKABLE_EXTS = {".txt", ".md", ".csv", ".html"}


def _process_s3_event(record):
    """S3 イベント:対象文書を PII マスキングして書き戻し、その後に取り込みを起動する。

    無限ループ防止:書き戻し時にメタデータ pii-masked=true を付け、
    既にマスキング済みのオブジェクトでは再マスキング・再書き戻しをしない。
    """
    import urllib.parse
    key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    ext = ("." + key.rsplit(".", 1)[1].lower()) if "." in key else ""

    # テキスト系のみマスキング対象。それ以外は素通しで取り込みのみ。
    if ext in _MASKABLE_EXTS:
        try:
            head = s3.head_object(Bucket=DOCUMENT_BUCKET, Key=key)
            already = head.get("Metadata", {}).get("pii-masked") == "true"
        except Exception as e:
            print(f"head_object failed for {key}: {e}")
            already = False

        if not already:
            try:
                obj = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=key)
                text = obj["Body"].read().decode("utf-8")
                masked = _mask_pii(text)
                s3.put_object(
                    Bucket=DOCUMENT_BUCKET,
                    Key=key,
                    Body=masked.encode("utf-8"),
                    ContentType=obj.get("ContentType", "text/plain"),
                    Metadata={"pii-masked": "true"},
                )
                # 書き戻しが新たな S3 イベントを発火するが、次回は already=True で
                # マスキングをスキップし取り込みのみ行うため、ループは1回で収束する。
                print(f"masked and rewrote {key}")
            except Exception as e:
                print(f"masking failed for {key}: {e}")

    _start_ingestion()


def _start_ingestion():
    """Knowledge Base のデータソースを再同期(取り込みジョブ起動)。"""
    try:
        resp = bedrock_agent.start_ingestion_job(
            knowledgeBaseId=KNOWLEDGE_BASE_ID, dataSourceId=DATA_SOURCE_ID,
        )
        job_id = resp["ingestionJob"]["ingestionJobId"]
        print(f"ingestion job started: {job_id}")
    except Exception as e:
        print(f"ingestion start failed: {e}")


# ========== HTTP: 質問 ==========
def _handle_question(body, user_id, request_id):
    question = body.get("question")
    if not question:
        return _response(400, {"error": "question is required"})
    response = bedrock_agent_runtime.retrieve_and_generate(
        input={"text": question},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": KNOWLEDGE_BASE_ID, "modelArn": MODEL_ARN,
            },
        },
    )
    answer = response["output"]["text"]
    citations = []
    for citation in response.get("citations", []):
        for ref in citation.get("retrievedReferences", []):
            s3_loc = ref.get("location", {}).get("s3Location", {})
            citations.append({
                "uri": s3_loc.get("uri"),
                "snippet": ref.get("content", {}).get("text", "")[:200],
            })
    _write_audit_log(user_id, question, answer, [c["uri"] for c in citations], request_id)
    return _response(200, {"answer": answer, "citations": citations})


# ========== HTTP: アップロード用 presigned URL 発行 ==========
def _handle_upload(body):
    filename = (body.get("filename") or "").strip()
    if not filename or "/" in filename or "\\" in filename:
        return _response(400, {"error": "valid filename is required"})
    ext = ("." + filename.rsplit(".", 1)[1].lower()) if "." in filename else ""
    if ext not in ALLOWED_EXTS:
        return _response(400, {"error": f"unsupported file type: {ext or 'none'}"})
    key = f"{DOCUMENT_PREFIX}{filename}"
    try:
        url = s3.generate_presigned_url(
            "put_object",
            Params={"Bucket": DOCUMENT_BUCKET, "Key": key},
            ExpiresIn=300,
        )
    except Exception as e:
        return _response(500, {"error": f"failed to create upload url: {e}"})
    return _response(200, {"upload_url": url, "key": key})


# ========== ルーティング ==========
def handler(event, context):
    # S3 イベント(取り込み起動)か HTTP リクエストかを判別
    if isinstance(event, dict) and event.get("Records"):
        first = event["Records"][0]
        if first.get("eventSource") == "aws:s3":
            for record in event["Records"]:
                _process_s3_event(record)
            return {"statusCode": 200}

    # 以降は HTTP(Lambda Function URL)
    method = event.get("requestContext", {}).get("http", {}).get("method", "").upper()
    if method == "OPTIONS":
        return {"statusCode": 204, "body": ""}

    try:
        claims = _verify_id_token(event)
    except AuthError:
        return _response(401, {"error": "unauthorized"})
    user_id = claims["sub"]
    request_id = getattr(context, "aws_request_id", str(uuid.uuid4()))

    try:
        body = json.loads(event.get("body") or "{}")
        action = body.get("action", "question")
        if action == "upload":
            return _handle_upload(body)
        return _handle_question(body, user_id, request_id)
    except Exception as e:
        return _response(500, {"error": str(e)})


def _response(status_code, body_dict):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body_dict, ensure_ascii=False),
    }
