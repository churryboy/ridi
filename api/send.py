"""
Vercel Serverless Function — Gmail API로 Proby 가이드라인 이메일 발송
환경변수: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
"""

import base64
import json
import os
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler

GMAIL_CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
GMAIL_CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
TOKEN_URI = "https://oauth2.googleapis.com/token"
FROM_EMAIL = os.environ.get("GMAIL_FROM_EMAIL", "chris@proby.io")


def get_access_token():
    """Refresh token으로 새 access token 발급."""
    import urllib.request
    import urllib.parse

    data = urllib.parse.urlencode({
        "client_id": GMAIL_CLIENT_ID,
        "client_secret": GMAIL_CLIENT_SECRET,
        "refresh_token": GMAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()

    req = urllib.request.Request(TOKEN_URI, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result["access_token"]


def gmail_send_raw(access_token: str, raw_message: str):
    """Gmail API로 raw 메시지 전송 (google-api-python-client 없이 직접 호출)."""
    import urllib.request

    body = json.dumps({"raw": raw_message}).encode("utf-8")
    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=body,
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def build_email_html(guideline_html: str) -> str:
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


def send_emails(access_token, to_emails, subject, html_body,
                attachment_data=None, attachment_name=None):
    sent = []
    errors = []

    for to_addr in to_emails:
        to_addr = to_addr.strip()
        if not to_addr or "@" not in to_addr:
            continue
        try:
            msg = MIMEMultipart("mixed")
            msg["To"] = to_addr
            msg["From"] = FROM_EMAIL
            msg["Subject"] = subject

            body_part = MIMEMultipart("alternative")
            body_part.attach(MIMEText(
                "Proby 인터뷰 가이드라인이 전달되었습니다. HTML 뷰어에서 확인해 주세요.",
                "plain", "utf-8"))
            body_part.attach(MIMEText(html_body, "html", "utf-8"))
            msg.attach(body_part)

            if attachment_data and attachment_name:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment_data)
                encoders.encode_base64(part)
                part.add_header("Content-Disposition",
                                f'attachment; filename="{attachment_name}"')
                msg.attach(part)

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
            gmail_send_raw(access_token, raw)
            sent.append(to_addr)
        except Exception as e:
            errors.append({"email": to_addr, "error": str(e)})

    return sent, errors


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if not all([GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN]):
            self._json(500, {"ok": False, "error": "Gmail 환경변수가 설정되지 않았습니다."})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "잘못된 JSON 형식입니다."})
            return

        emails = data.get("emails", [])
        guideline_html = data.get("guidelineHtml", "")
        title = data.get("title", "인터뷰 가이드라인")
        file_base64 = data.get("fileBase64", "")
        file_name = data.get("fileName", "")

        if not emails:
            self._json(400, {"ok": False, "error": "이메일 주소가 없습니다."})
            return
        if not guideline_html:
            self._json(400, {"ok": False, "error": "가이드라인 내용이 없습니다."})
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
            access_token = get_access_token()
            email_body = build_email_html(guideline_html)
            subject = f"[Proby] {title}"
            sent, errors = send_emails(access_token, emails, subject, email_body,
                                       attachment_data, attachment_name)
            self._json(200, {
                "ok": True,
                "sent": sent,
                "errors": errors,
                "message": f"{len(sent)}명에게 발송 완료" + (f", {len(errors)}건 실패" if errors else "")
            })
        except Exception as e:
            self._json(500, {"ok": False, "error": str(e)})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
