"""DashScope 标注客户端：统一封装并发调用、重试、结构化输出校验、按 (模型, 模式, 提示词哈希) 缓存、
费用与 token 统计。CLAUDE.md 4.4 节的落地实现。

关键实测结论（2026-09-27，见 docs/decisions.md D4）：
  qwen3.8-max / qwen3.8-flash / qwen3.7-plus / deepseek-v4.1-flash 默认开启思考模式
  （即使不传 enable_thinking 也会产生 reasoning_tokens）。因此本客户端**总是显式传递**
  enable_thinking，不依赖模型默认值。

API key 只从环境变量 DASHSCOPE_API_KEY 读取，绝不写入文件、日志或缓存。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

import requests

DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

# 人民币元 / 百万 tokens。来源：阿里云百炼定价页（2026-09-27 查阅），deepseek-v4.1-flash
# 价格为公开定价页对同系列 flash 档位的对照估算，非官方逐模型页面直接确认，标注为近似值，
# 正式标注前应在 docs/decisions.md 核对最新账单一次。
PRICING: dict[str, dict[str, Decimal]] = {
    "qwen3.8-max": {"input": Decimal("12"), "output": Decimal("36")},
    "qwen3.8-flash": {"input": Decimal("0.8"), "output": Decimal("2.7")},
    "qwen3.7-plus": {"input": Decimal("2"), "output": Decimal("8")},
    "deepseek-v4.1-flash": {"input": Decimal("1"), "output": Decimal("2")},
}


@dataclass
class AnnotationRequest:
    request_id: str
    model: str
    thinking: bool
    messages: list[dict[str, str]]
    response_schema_validator: Optional[Callable[[Any], bool]] = None
    max_tokens: int = 1024
    temperature: float = 0.0
    extra_params: dict = field(default_factory=dict)


@dataclass
class AnnotationResult:
    request_id: str
    model: str
    thinking: bool
    prompt_hash: str
    ok: bool
    parsed: Any = None
    raw_text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cny: Decimal = Decimal("0")
    cached: bool = False
    error: Optional[str] = None
    attempts: int = 0


def _prompt_hash(model: str, thinking: bool, messages: list[dict], extra: dict) -> str:
    payload = json.dumps(
        {"model": model, "thinking": thinking, "messages": messages, "extra": extra},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AnnotationClient:
    def __init__(
        self,
        cache_dir: str | Path = ".cache",
        max_workers: int = 8,
        max_retries: int = 5,
        base_delay: float = 1.0,
        timeout: float = 60.0,
    ):
        self.api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise RuntimeError("环境变量 DASHSCOPE_API_KEY 未设置")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.timeout = timeout
        self._slowdown_lock = threading.Lock()
        self._extra_delay = 0.0  # 全局自适应退避，遇 429 时增加

    # ---- 缓存 ----

    def _cache_path(self, prompt_hash: str) -> Path:
        return self.cache_dir / prompt_hash[:2] / f"{prompt_hash}.json"

    def _load_cache(self, prompt_hash: str) -> Optional[dict]:
        p = self._cache_path(prompt_hash)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def _save_cache(self, prompt_hash: str, record: dict) -> None:
        p = self._cache_path(prompt_hash)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 单次调用 ----

    def _post(self, req: AnnotationRequest) -> requests.Response:
        payload = {
            "model": req.model,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "enable_thinking": req.thinking,
            **req.extra_params,
        }
        return requests.post(
            DASHSCOPE_URL,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )

    def _call_one(self, req: AnnotationRequest) -> AnnotationResult:
        prompt_hash = _prompt_hash(req.model, req.thinking, req.messages, req.extra_params)
        cached = self._load_cache(prompt_hash)
        if cached is not None:
            return AnnotationResult(
                request_id=req.request_id,
                model=req.model,
                thinking=req.thinking,
                prompt_hash=prompt_hash,
                ok=cached["ok"],
                parsed=cached.get("parsed"),
                raw_text=cached.get("raw_text", ""),
                input_tokens=cached.get("input_tokens", 0),
                output_tokens=cached.get("output_tokens", 0),
                cost_cny=Decimal(str(cached.get("cost_cny", "0"))),
                cached=True,
                error=cached.get("error"),
            )

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            with self._slowdown_lock:
                delay = self._extra_delay
            if delay:
                time.sleep(delay)
            try:
                resp = self._post(req)
            except requests.RequestException as e:
                last_error = f"network error: {e}"
                time.sleep(self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
                continue

            if resp.status_code == 429:
                with self._slowdown_lock:
                    self._extra_delay = min(self._extra_delay + 0.5, 10.0)
                last_error = "rate limited (429)"
                time.sleep(self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
                continue

            if resp.status_code != 200:
                last_error = f"http {resp.status_code}: {resp.text[:300]}"
                time.sleep(self.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
                continue

            data = resp.json()
            usage = data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            try:
                raw_text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError):
                last_error = f"unexpected response shape: {json.dumps(data, ensure_ascii=False)[:300]}"
                time.sleep(self.base_delay * (2 ** (attempt - 1)))
                continue

            parsed = _try_parse_json(raw_text)
            if req.response_schema_validator is not None:
                if parsed is None or not req.response_schema_validator(parsed):
                    last_error = "response failed schema validation"
                    # 校验失败重试：追加更严格的格式提醒
                    req.messages = req.messages + [
                        {"role": "assistant", "content": raw_text},
                        {"role": "user", "content": "上面的回答不是合法 JSON 或不满足要求的字段，请只输出合法 JSON，不要有多余文字。"},
                    ]
                    continue

            price = PRICING.get(req.model, {"input": Decimal("0"), "output": Decimal("0")})
            cost = (Decimal(input_tokens) / Decimal(1_000_000)) * price["input"] + (
                Decimal(output_tokens) / Decimal(1_000_000)
            ) * price["output"]

            record = {
                "ok": True,
                "parsed": parsed,
                "raw_text": raw_text,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_cny": str(cost),
                "error": None,
            }
            self._save_cache(prompt_hash, record)
            return AnnotationResult(
                request_id=req.request_id,
                model=req.model,
                thinking=req.thinking,
                prompt_hash=prompt_hash,
                ok=True,
                parsed=parsed,
                raw_text=raw_text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_cny=cost,
                cached=False,
                attempts=attempt,
            )

        return AnnotationResult(
            request_id=req.request_id,
            model=req.model,
            thinking=req.thinking,
            prompt_hash=prompt_hash,
            ok=False,
            error=last_error,
            attempts=self.max_retries,
        )

    # ---- 批量调用 ----

    def run_batch(self, requests_: list[AnnotationRequest]) -> list[AnnotationResult]:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, AnnotationResult] = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self._call_one, r): r.request_id for r in requests_}
            for future in as_completed(futures):
                rid = futures[future]
                results[rid] = future.result()
        return [results[r.request_id] for r in requests_]


def _try_parse_json(text: str) -> Optional[Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None
