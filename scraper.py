import html
import json
import os
import re
import smtplib
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent
TARGET_URL = "https://www.esunbank.com/zh-tw/personal/credit-card/discount/shopInfo?sno=2100_06"
GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com/chat/completions"
REQUEST_TIMEOUT = 30
MAX_ERROR_BODY_CHARS = 4000


@dataclass
class ModelResult:
    model: str
    promos: list[dict[str, Any]]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _truncate(text: str, limit: int = MAX_ERROR_BODY_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}... [truncated {len(text) - limit} chars]"


def _response_body(response: requests.Response | None) -> str:
    if response is None:
        return ""
    try:
        return _truncate(response.text or "")
    except Exception:
        return "<unable to read response body>"


def _raise_for_http_error(response: requests.Response, context: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = _response_body(exc.response)
        status = exc.response.status_code if exc.response is not None else "unknown"
        raise RuntimeError(f"{context} failed with HTTP {status}. Response body: {body}") from exc


def fetch_esun_html() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
        )
    }
    response = requests.get(TARGET_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    _raise_for_http_error(response, "Fetching E.SUN promotion page")
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def parse_esun_html(page_html: str) -> tuple[list[dict[str, str]], str]:
    soup = BeautifulSoup(page_html, "html.parser")
    blocks: list[str] = []
    activities: list[dict[str, str]] = []

    for title_tag in soup.select("p.pt-3"):
        title = title_tag.get_text(" ", strip=True)
        list_tag = title_tag.find_next_sibling("ul", class_="greenBulletList")
        if not title or list_tag is None:
            continue

        item_texts = [li.get_text(" ", strip=True) for li in list_tag.select("li")]
        combined = " ".join(item_texts)

        if not _looks_like_registration_block(title, combined):
            continue

        blocks.append(str(title_tag))
        blocks.append(str(list_tag))

        activities.append(
            {
                "name": title,
                "quota": _extract_quota(combined),
                "time": _extract_times(combined),
                "benefits": "<br>".join(_extract_benefits(item_texts)) or combined[:300],
            }
        )

    raw_promo_html = "\n".join(blocks)
    return activities, raw_promo_html


def _looks_like_registration_block(title: str, body: str) -> bool:
    text = f"{title} {body}"
    keywords = ("登錄", "名額", "限量", "回饋", "優惠", "活動")
    return any(keyword in text for keyword in keywords)


def _extract_times(text: str) -> str:
    patterns = [
        r"\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2}(?:\s*~\s*\d{4}/\d{1,2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2})?",
        r"\d{4}/\d{1,2}/\d{1,2}(?:\s*~\s*\d{4}/\d{1,2}/\d{1,2})?",
    ]
    matches: list[str] = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, text))
    return " / ".join(dict.fromkeys(matches)) if matches else "未標示"


def _extract_quota(text: str) -> str:
    matches = re.findall(r"(?:限量|名額|前)\s*[\d,]+\s*(?:名|份|組)?", text)
    if not matches:
        matches = re.findall(r"[\d,]+\s*(?:名|份|組)", text)
    return " / ".join(dict.fromkeys(matches)) if matches else "未標示"


def _extract_benefits(items: list[str]) -> list[str]:
    keywords = ("回饋", "折抵", "刷卡金", "e point", "點", "滿", "登錄")
    benefits = []
    for item in items:
        if any(keyword in item for keyword in keywords):
            benefits.append(html.escape(item))
    return benefits[:8]


def model_candidates() -> list[str]:
    primary = os.environ.get("GITHUB_MODELS_PRIMARY", "gpt-4o").strip()
    fallback_raw = os.environ.get("GITHUB_MODELS_FALLBACKS", "gpt-4o-mini").strip()
    candidates = [primary]
    candidates.extend(model.strip() for model in fallback_raw.split(",") if model.strip())
    return list(dict.fromkeys(candidates))


def ask_github_models(raw_promo_html: str) -> ModelResult:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set; skip AI promotion extraction.")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    system_prompt = (
        "你是台灣信用卡優惠資料整理助手。請把輸入的玉山銀行活動 HTML 整理成 JSON Array，"
        "只輸出 JSON，不要 Markdown。每筆物件欄位固定為："
        "id, name, group, threshold, type, value, cap, checked。"
        "threshold/value/cap 必須是數字；type 只能是 fixed 或 percent；checked 一律 true。"
        "同一個階梯式活動請使用相同 group，讓前端只套用達標的最高門檻。"
    )
    user_prompt = (
        "請從以下 HTML 找出可用於價差試算的刷卡/登錄優惠。"
        "若資訊不足，保守估計門檻與回饋，名稱需保留來源活動重點。\n\n"
        f"{raw_promo_html}"
    )

    failures: list[str] = []
    for model in model_candidates():
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }
        print(f"Calling GitHub Models with {model}...")
        try:
            response = requests.post(
                GITHUB_MODELS_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code >= 500:
                failures.append(
                    f"{model}: HTTP {response.status_code}; body: {_response_body(response)}"
                )
                continue
            _raise_for_http_error(response, f"GitHub Models request ({model})")

            content = _extract_message_content(response.json())
            if not content:
                failures.append(f"{model}: empty model response")
                continue

            return ModelResult(model=model, promos=_parse_model_json(content))
        except requests.RequestException as exc:
            response = getattr(exc, "response", None)
            failures.append(f"{model}: request error {exc}; body: {_response_body(response)}")
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            failures.append(f"{model}: invalid model output: {exc}")

    raise RuntimeError("All GitHub Models attempts failed:\n- " + "\n- ".join(failures))


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return str(message.get("content") or "").strip()


def _parse_model_json(content: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", content.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("model output is not a JSON array")
    return [_normalize_promo(item, index) for index, item in enumerate(parsed, start=1)]


def _normalize_promo(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"promo #{index} is not an object")

    promo_type = item.get("type") if item.get("type") in {"fixed", "percent"} else "fixed"
    value = _number(item.get("value"))
    cap = _number(item.get("cap"), value if promo_type == "fixed" else 99999999)
    return {
        "id": str(item.get("id") or f"promo_{index}"),
        "name": str(item.get("name") or f"優惠 {index}"),
        "group": str(item.get("group") or item.get("name") or f"優惠 {index}"),
        "threshold": _number(item.get("threshold")),
        "type": promo_type,
        "value": value,
        "cap": cap,
        "checked": bool(item.get("checked", True)),
    }


def _number(value: Any, default: float = 0) -> float:
    if isinstance(value, (int, float)):
        return value
    if value is None:
        return default
    text = re.sub(r"[^\d.-]", "", str(value))
    try:
        number = float(text)
    except ValueError:
        return default
    return int(number) if number.is_integer() else number


def update_index(promos: list[dict[str, Any]], model_name: str) -> None:
    html_path = ROOT / "index.html"
    html_content = html_path.read_text(encoding="utf-8")
    promo_json = json.dumps(promos, ensure_ascii=False, indent=6)
    update_time = _now()

    # 注入全域變數，而不是替換固定的陣列結構
    js_injection = f"""
<script>
  window.MSIM_PROMOS = {promo_json};
  window.MSIM_AI_MODEL = "{model_name}";
  window.MSIM_UPDATE_TIME = "{update_time}";
</script>
"""
    # 將注入腳本放在 </head> 標籤之前
    head_end_pattern = re.compile(r"(\s*)</head>", re.IGNORECASE)
    match = head_end_pattern.search(html_content)
    
    if not match:
        raise RuntimeError("Could not find </head> tag in index.html to inject data.")

    # 移除舊的、可能存在的注入腳本，避免重複
    script_pattern = re.compile(r"<script>\s*window\.MSIM_PROMOS\s*=\s*\[[\s\S]*?\];[\s\S]*?</script>", re.DOTALL)
    html_content = script_pattern.sub("", html_content)

    # 插入新的注入腳本
    html_content = head_end_pattern.sub(f"{js_injection}{match.group(1)}</head>", html_content, count=1)

    html_path.write_text(html_content, encoding="utf-8")
    print(f"Updated index.html with {len(promos)} promos from {model_name} at {update_time}.")


def write_discount_snapshot(activities: list[dict[str, str]]) -> None:
    output = {"last_updated": _now(), "source": TARGET_URL, "data": activities}
    (ROOT / "esun_discounts.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote esun_discounts.json with {len(activities)} parsed activity blocks.")


def send_email(activities: list[dict[str, str]]) -> None:
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        print("Gmail credentials are not set; skip email notification.")
        return

    rows = "\n".join(
        f"<tr><td>{html.escape(act['name'])}</td><td>{html.escape(act['quota'])}</td>"
        f"<td>{html.escape(act['time'])}</td><td>{act['benefits']}</td></tr>"
        for act in activities
    )
    body = f"""
    <html><body>
      <h2>玉山優惠同步完成</h2>
      <p>更新時間：{_now()}</p>
      <table border="1" cellpadding="8" cellspacing="0">
        <tr><th>活動</th><th>名額</th><th>登錄時間</th><th>重點</th></tr>
        {rows}
      </table>
    </body></html>
    """

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = gmail_user
    msg["Subject"] = f"MSIM 玉山優惠同步完成 ({datetime.now().strftime('%m/%d')})"
    msg.attach(MIMEText(body, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=REQUEST_TIMEOUT) as server:
        server.starttls()
        server.login(gmail_user, gmail_password)
        server.send_message(msg)
    print("Sent email notification.")


def main() -> None:
    page_html = fetch_esun_html()
    activities, raw_promo_html = parse_esun_html(page_html)
    if not activities:
        raise RuntimeError("No E.SUN promotion blocks were parsed from the source page.")

    write_discount_snapshot(activities)
    send_email(activities)

    if raw_promo_html:
        result = ask_github_models(raw_promo_html)
        update_index(result.promos, result.model)


if __name__ == "__main__":
    main()
