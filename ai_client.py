import json
import re
import urllib.error
import urllib.request

from flask import current_app


class AIClientError(RuntimeError):
    pass


def chat_json(messages, temperature=0.2, max_tokens=1200):
    api_key = current_app.config.get("AI_API_KEY", "")
    if not api_key:
        raise AIClientError("AI_API_KEY is not configured")

    base_url = str(current_app.config.get("AI_BASE_URL", "https://api.deepseek.com")).rstrip("/")
    model = current_app.config.get("AI_MODEL", "deepseek-chat")
    timeout = int(current_app.config.get("AI_TIMEOUT", 20))
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    endpoint = f"{base_url}/chat/completions" if base_url.endswith("/v1") else f"{base_url}/v1/chat/completions"
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise AIClientError(f"AI request failed: {exc.code} {detail[:300]}") from exc
    except Exception as exc:
        raise AIClientError(f"AI request failed: {exc}") from exc

    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise AIClientError("AI response shape is invalid") from exc

    return parse_json_content(content)


def parse_json_content(content):
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise AIClientError("AI response is not JSON")
        return json.loads(match.group(0))
