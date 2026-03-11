#!/usr/bin/env python3
"""
RIDI Proby Connector — 로컬 서버
HTML 정적 파일 서빙 + /api/send 엔드포인트로 Gmail API 이메일 발송
"""

import base64
import json
import os
import sys
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email import encoders
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VAULT_ROOT = SCRIPT_DIR.parent.parent
SURVEY_SKILL_DIR = VAULT_ROOT / "Skills" / "설문 연동"

PORT = 8765


def _load_dotenv():
    """프로젝트 루트 .env를 읽어 os.environ에 넣음 (ANTHROPIC_API_KEY 등)."""
    env_path = VAULT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip()
            if k and v and v.startswith('"') and v.endswith('"'):
                v = v[1:-1]
            if k and v:  # 빈 값은 설정하지 않음 → .env에 ANTHROPIC_API_KEY= 빈 줄이 있어도 다음 줄의 실제 키가 적용됨
                os.environ[k] = v
    except Exception:
        pass


def get_gmail_credentials():
    """기존 설문 연동 스크립트의 OAuth 토큰을 재사용."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("필요 패키지: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2", file=sys.stderr)
        sys.exit(1)

    SCOPES = [
        "https://www.googleapis.com/auth/gmail.send",
    ]
    token_path = SURVEY_SKILL_DIR / "token_forms.json"
    creds_path = SURVEY_SKILL_DIR / "credentials.json"
    if not creds_path.exists():
        fallback = VAULT_ROOT / "Skills" / "이메일 스크랩" / "credentials.json"
        creds_path = fallback if fallback.exists() else creds_path
    if not creds_path.exists():
        raise FileNotFoundError("credentials.json을 찾을 수 없습니다.")

    creds = None
    if token_path.exists():
        stored = json.loads(token_path.read_text(encoding="utf-8"))
        stored_scopes = stored.get("scopes") or []
        if "https://www.googleapis.com/auth/gmail.send" in stored_scopes:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            ALL_SCOPES = [
                "https://www.googleapis.com/auth/forms.body",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/gmail.send",
            ]
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), ALL_SCOPES)
            creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
    return creds


def send_email(creds, to_emails: list, subject: str, html_body: str,
               attachment_data: bytes = None, attachment_name: str = None,
               from_email: str = "chris@proby.io"):
    """Gmail API로 HTML 이메일 발송 (원본 파일 첨부 지원)."""
    from googleapiclient.discovery import build

    gmail = build("gmail", "v1", credentials=creds)
    sent = []
    errors = []

    for to_addr in to_emails:
        to_addr = to_addr.strip()
        if not to_addr or "@" not in to_addr:
            continue
        try:
            msg = MIMEMultipart("mixed")
            msg["To"] = to_addr
            msg["From"] = from_email
            msg["Subject"] = subject

            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText("Proby 인터뷰 가이드라인이 전달되었습니다. HTML 뷰어에서 확인해 주세요.", "plain", "utf-8"))
            body_part.attach(MIMEText(html_body, "html", "utf-8"))
            msg.attach(body_part)

            if attachment_data and attachment_name:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment_data)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
                msg.attach(part)
                print(f"  첨부: {attachment_name} ({len(attachment_data)} bytes)")

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
            gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
            sent.append(to_addr)
            print(f"  발송 완료: {to_addr}")
        except Exception as e:
            errors.append({"email": to_addr, "error": str(e)})
            print(f"  발송 실패: {to_addr} — {e}")

    return sent, errors


def build_email_html(guideline_html: str) -> str:
    """가이드라인 HTML을 이메일에 적합한 완전한 HTML 문서로 래핑."""
    return f"""<!DOCTYPE html>
<html lang="ko">
<head><meta charset="UTF-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #333; line-height: 1.7; max-width: 700px; margin: 0 auto; padding: 32px;">
    <div style="border-bottom: 3px solid #1F8CE6; padding-bottom: 12px; margin-bottom: 24px;">
        <h2 style="margin: 0; color: #1F8CE6;">Proby 인터뷰 가이드라인</h2>
        <p style="margin: 4px 0 0; font-size: 13px; color: #999;">RIDI Proby Connector에서 자동 생성되었습니다.</p>
    </div>
    {guideline_html}
    <div style="border-top: 1px solid #E5E8EB; padding-top: 16px; margin-top: 32px; font-size: 12px; color: #999;">
        이 이메일은 RIDI Proby Connector를 통해 발송되었습니다.
    </div>
</body>
</html>"""


def _path_base(path):
    """쿼리 제외, 앞뒤 슬래시 정규화 (예: /api/evaluate?x=1 → /api/evaluate)."""
    p = (path or "").split("?")[0].strip().strip("/")
    return "/" + p if p else "/"


class ConnectorHandler(SimpleHTTPRequestHandler):
    def __init__(self, request, client_address, server):
        # 프로젝트 루트가 아닌 server.py 있는 폴더(리디)에서 정적 파일 서빙
        super().__init__(request, client_address, server, directory=str(SCRIPT_DIR))

    def do_GET(self):
        base = _path_base(self.path)
        if base in ("/api/send", "/api/evaluate"):
            self.send_response(405)
            self.send_header("Allow", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            return
        # 브라우저 자동 요청 경로 — 빈 응답으로 처리해 404 노이즈 제거
        if base in ("/favicon.ico", "/.well-known/appspecific/com.chrome.devtools.json"):
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def do_POST(self):
        base = _path_base(self.path)
        if base == "/api/send":
            self._handle_send()
        elif base == "/api/evaluate":
            self._handle_evaluate()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        base = _path_base(self.path)
        if base in ("/api/send", "/api/evaluate"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            return
        self.send_error(404)

    def _handle_send(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(400, {"ok": False, "error": "잘못된 JSON 형식입니다."})
            return

        emails = data.get("emails", [])
        guideline_html = data.get("guidelineHtml", "")
        title = data.get("title", "인터뷰 가이드라인")
        file_base64 = data.get("fileBase64", "")
        file_name = data.get("fileName", "")

        if not emails:
            self._json_response(400, {"ok": False, "error": "이메일 주소가 없습니다."})
            return
        if not guideline_html:
            self._json_response(400, {"ok": False, "error": "가이드라인 내용이 없습니다."})
            return

        attachment_data = None
        attachment_name = None
        if file_base64 and file_name:
            try:
                attachment_data = base64.b64decode(file_base64)
                attachment_name = file_name
            except Exception:
                pass

        try:
            print(f"\n이메일 발송 시작 ({len(emails)}명)...")
            creds = get_gmail_credentials()
            email_body = build_email_html(guideline_html)
            subject = f"[Proby] {title}"
            sent, errors = send_email(creds, emails, subject, email_body,
                                      attachment_data=attachment_data,
                                      attachment_name=attachment_name)

            self._json_response(200, {
                "ok": True,
                "sent": sent,
                "errors": errors,
                "message": f"{len(sent)}명에게 발송 완료" + (f", {len(errors)}건 실패" if errors else "")
            })
        except Exception as e:
            print(f"발송 오류: {e}")
            self._json_response(500, {"ok": False, "error": str(e)})

    def _handle_evaluate(self):
        import urllib.request
        import urllib.error

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(400, {"ok": False, "error": "잘못된 JSON 형식입니다."})
            return
        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            self._json_response(400, {"ok": False, "error": "면접 전사/내용이 없습니다."})
            return

        criteria_path = VAULT_ROOT / "60. 유저인사이트팀" / "리디 바레이저" / "기준.md"
        criteria = criteria_path.read_text(encoding="utf-8") if criteria_path.exists() else ""

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        print(f"[evaluate] API key 로드: {'OK (' + api_key[:8] + '...)' if api_key else '비어있음'}")
        if not api_key:
            self._json_response(500, {"ok": False, "error": "ANTHROPIC_API_KEY 환경변수를 설정해 주세요."})
            return

        system = """당신은 리디의 바레이저(Bar Raiser) 면접 평가자입니다.
아래 기준에 따라 면접 전사를 평가하고, 지정된 마크다운 형식으로만 출력하세요.

━━━━━━━━━━━━━━━━━━━━
## 1단계: 아래 Pass 증거를 면접 전사에서 먼저 찾으세요
━━━━━━━━━━━━━━━━━━━━

다음 증거 유형이 하나라도 있으면 해당 영역은 **Pass**입니다.

[Decision making Pass 증거]
① 팀/상사와 다른 방향을 데이터·논거로 직접 제안해서, 팀이 채택하거나 KPI/성과가 개선된 사례
② 단기 압박에 맞서 장기 관점을 데이터로 설득해 관철시킨 사례
③ 정성 데이터(SNS 반응, 사용자 의견 등)와 정량 데이터를 조합해 의사결정에 활용한 구체 사례

[Communication Pass 증거]
① 찜찜한 문제를 발견하고, 동료 설득 → 구조 개선 제안 → 상급자 문제제기 중 2단계 이상 실행한 사례
② 불편한 사실을 데이터/통계 등 근거를 갖추어 동료나 상사에게 직접 말한 사례
③ 조직 구조 변화를 위해 실질적 행동(퇴사 포함)을 취한 사례
   ※ 퇴사는 "조직이 바뀌지 않아 커리어를 걸고 이탈한 행동"이므로 Pass 근거로 인정

[Work ethics Pass 증거]
① 특정 기술·도구(SQL, AI 등)를 스스로 습득해서 실제 업무 효율이나 성과가 개선된 사례
② 성공/실패한 업무 결과를 데이터로 직접 파고들어 원인을 분석한 사례
③ 업무에서 AI(GPT, Gemini 등)나 새로운 도구를 실제로 활용해 결과물을 낸 사례

━━━━━━━━━━━━━━━━━━━━
## 2단계: Fail 확정 조건 (매우 엄격하게만 적용)
━━━━━━━━━━━━━━━━━━━━

아래에 **모두** 해당할 때만 해당 영역은 Fail:
- 위 Pass 증거가 하나도 발견되지 않는다
- 구체 사례(상황+행동+결과) 없이 태도/습관 서술("~하는 편", "~하려고 노력")만 있다

⚠️ 절대 Fail 근거로 쓰면 안 되는 것:
- "더 잘할 수 있었을 것 같다", "그때 더 강하게 했으면" 등의 자기반성 발언
- 조직이 변하지 않았거나 문제가 해결되지 않은 결과 (행동을 했다면 Pass)
- 후회, 아쉬움 표현 (이는 성장 마인드셋 신호)

━━━━━━━━━━━━━━━━━━━━
## 3단계: 종합 판정
━━━━━━━━━━━━━━━━━━━━
- Decision making / Communication / Work ethics 중 **2개 이상 Pass → 종합 Pass 권고**
- 2개 이상 Fail → 종합 Fail 권고

━━━━━━━━━━━━━━━━━━━━
## 출력 형식 (반드시 이 구조로만)
━━━━━━━━━━━━━━━━━━━━

### 01. Decision making (의사결정)
- **[원칙 번호] 원칙명** — Pass ✓ / Fail ✗
  > "후보자 발언 직접 인용"
  판단 근거 한 줄

### 02. Communication (소통)
- **[원칙 번호] 원칙명** — Pass ✓ / Fail ✗
  > "후보자 발언 직접 인용"
  판단 근거 한 줄

### 03. Work ethics (업무자세)
- **[원칙 번호] 원칙명** — Pass ✓ / Fail ✗
  > "후보자 발언 직접 인용"
  판단 근거 한 줄

### 종합 의견
강점 요약 / 미달 사유(있다면) / **Pass 권고** 또는 **Fail 권고** 명시"""

        user = f"[평가 기준]\n{criteria}\n\n[면접 전사/내용]\n{transcript[:12000]}"

        req_body = json.dumps({
            "model": "claude-3-haiku-20240307",
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": user}]
        }).encode("utf-8")

        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=req_body, method="POST")
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read())
            evaluation = result["content"][0]["text"].strip()
            self._json_response(200, {"ok": True, "evaluation": evaluation})
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"[evaluate] Anthropic HTTPError {e.code}: {err_body}")
            self._json_response(500, {"ok": False, "error": f"Anthropic API 오류 {e.code}: {err_body[:300]}"})
        except Exception as e:
            print(f"[evaluate] 오류: {type(e).__name__}: {e}")
            self._json_response(500, {"ok": False, "error": str(e)})

    def _json_response(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # args[0]이 HTTPStatus enum일 수 있어 str()로 변환 후 비교
        first = str(args[0]) if args else ""
        if "/api/" in first:
            super().log_message(format, *args)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    import subprocess
    _load_dotenv()
    os.chdir(SCRIPT_DIR)

    try:
        result = subprocess.run(["lsof", f"-ti:{PORT}"], capture_output=True, text=True)
        pids = result.stdout.strip()
        if pids:
            print(f"포트 {PORT} 사용 중 — 기존 프로세스({pids}) 종료 중...")
            for pid in pids.split("\n"):
                pid = pid.strip()
                if pid and pid != str(os.getpid()):
                    os.kill(int(pid), 9)
            import time; time.sleep(1)
    except Exception:
        pass

    server = ReusableHTTPServer(("localhost", PORT), ConnectorHandler)
    print(f"RIDI Proby Connector 서버 시작: http://localhost:{PORT}")
    print("종료하려면 Ctrl+C를 누르세요.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")
        server.server_close()
