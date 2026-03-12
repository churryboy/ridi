"""
Vercel Serverless Function — 면접 전사/내용을 리디 바레이저 기준.md로 LLM 평가
환경변수: ANTHROPIC_API_KEY
"""

import json
import os
from http.server import BaseHTTPRequestHandler

CRITERIA = """
# 리디 바레이저 평가 기준

## 01. Decision making (의사결정)
- 1.1 가장 먼저 그리고 마지막엔 고객 관점에서 바라본다
- 1.2 장기적인 관점으로 생각한다
- 1.3 개인이나 팀의 목표보다 전사의 목표를 우선시한다
  - Pass: 전사/서비스 목표를 더 잘 달성하기 위한 새로운 전략·방법을 스스로 설계하고, 데이터/결과로 입증한 사람
  - Fail: 조직 목표를 위해 더 나은 방식을 주도적으로 설계·입증한 사례가 없는 경우
- 1.4 가장 잘 아는 사람이 결정하고 책임은 조직장이 진다
- 1.5 데이터와 직관을 동시에 활용한다

## 02. Communication (소통)
- 2.1 의도와 맥락을 중심으로 이야기한다
- 2.2 표준화된 용어, 정확한 숫자, 통일된 날짜 표기법을 사용한다
- 2.3 찜찜한 상황에선 반드시 먼저 이야기를 꺼낸다
  - Pass: 찜찜한 이슈 발견 시 동료 논의 → 구조/프로세스 변경 제안 → 상급자 문제 제기까지 이어지는 행동
  - Fail: 조직의 잘못된 기준·구조를 바꿔 보려는 장기적 행동·시도가 부족한 경우
- 2.4 불편함을 감수하더라도 솔직하게 이야기한다
- 2.5 소통 상대에게 존중하는 자세를 유지한다

## 03. Work ethics (업무자세)
- 3.1 의도를 명확히 정하고 일을 한다
- 3.2 도전적인 목표를 설정한다
- 3.3 탁월한 결과를 만들기 위해 집요하게 파고든다
  - Pass: 집요함이 비즈니스 성과·프로세스/도구 개선 등 조직/성과 개선 구체 사례로 드러나는 사람
  - Fail: 집요함이 실제 사업 임팩트나 조직 개선으로 연결된 증거가 약한 경우
- 3.4 최소의 자원으로 최대한 빠르게 의도를 달성한다
- 3.5 체력을 적극적으로 관리한다
"""


def evaluate_with_anthropic(transcript: str) -> str:
    import urllib.request
    import urllib.error

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다. Vercel 대시보드에서 설정 후 재배포해 주세요."

    system = """당신은 리디 면접 평가자입니다. 주어진 [평가 기준]에 따라 [면접 전사/내용]을 평가해 주세요.
평가 결과는 반드시 다음 형식의 마크다운으로 작성해 주세요:

### 01. Decision making (의사결정)
- 각 원칙(1.1~1.5)별로 면접 내용에서 드러난 사례나 언급을 요약하고, Pass/Fail 관점에서 간단히 판단과 근거를 적어 주세요.

### 02. Communication (소통)
- 각 원칙(2.1~2.5)별로 동일하게 요약·판단·근거를 적어 주세요.

### 03. Work ethics (업무자세)
- 각 원칙(3.1~3.5)별로 동일하게 요약·판단·근거를 적어 주세요.

### 종합 의견
- 2~3문단으로 종합 의견(강점, 보완점, Pass/Fail 권고)을 작성해 주세요."""

    user = f"""[평가 기준]\n{CRITERIA}\n\n[면접 전사/내용]\n{transcript[:12000]}"""

    # 3.5/4.x 사용 가능 여부는 워크스페이스별로 다름. 순서대로 시도.
    models_to_try = [
        "claude-3-5-sonnet-latest",
        "claude-3-5-haiku-latest",
        "claude-haiku-4-5",
        "claude-3-haiku-20240307",
    ]
    last_error = None
    for model in models_to_try:
        body = json.dumps({
            "model": model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": user}]
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            method="POST",
        )
        req.add_header("x-api-key", api_key)
        req.add_header("anthropic-version", "2023-06-01")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
            return data["content"][0]["text"].strip()
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_error = (e.code, err_body)
            if e.code == 404:  # 모델 미지원 → 다음 모델 시도
                continue
            raise RuntimeError(f"Anthropic API 오류 {e.code}: {err_body[:200]}")
    if last_error:
        code, err_body = last_error
        raise RuntimeError(f"Anthropic API 오류 {code}: {err_body[:200]}")


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "잘못된 JSON 형식입니다."})
            return

        transcript = (data.get("transcript") or "").strip()
        if not transcript:
            self._json(400, {"ok": False, "error": "면접 전사/내용이 없습니다."})
            return

        try:
            evaluation = evaluate_with_anthropic(transcript)
            if evaluation.startswith("ANTHROPIC_API_KEY"):
                self._json(500, {"ok": False, "error": evaluation})
                return
            self._json(200, {"ok": True, "evaluation": evaluation})
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
