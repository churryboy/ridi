#!/usr/bin/env python3
"""
RIDI Proby Connector — 로컬 서버
HTML 정적 파일 서빙 + /api/send 엔드포인트로 Gmail API 이메일 발송
"""

import base64
import json
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


class ConnectorHandler(SimpleHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/api/send":
            self._handle_send()
        else:
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

    def _json_response(self, status: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(format, *args)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    import os
    import subprocess
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
