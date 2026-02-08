from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime, timedelta
import os
from pathlib import Path
from openai import OpenAI
try:
    from aios.secret import get_secret
except Exception:
    def get_secret(name: str) -> Optional[str]:
        return os.getenv(name)
import json


class LLMProvider(str, Enum):
    OPENAI = "openai"
    GROK = "grok"


class BaseLLMProvider(ABC):
    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], model: Optional[str] = None, **kwargs) -> str:
        """
        messages = [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "..."}
        ]
        """
        pass


class LLMCaller:
    def __init__(
        self,
        provider: LLMProvider,
        provider_impl: BaseLLMProvider,
    ):
        self.provider = provider
        self.provider_impl = provider_impl

    def chat(self, messages: List[Dict[str, str]], model: Optional[str] = None, **kwargs) -> str:
        return self.provider_impl.chat(messages=messages, model=model, **kwargs)


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key=None):
        key = api_key or get_secret("OPENAI_APIKEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            # PoC convenience: load aegis-ai-core/ai/.env when running scripts directly.
            env_path = Path(__file__).resolve().with_name(".env")
            if env_path.exists():
                try:
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k and os.getenv(k) is None:
                            os.environ[k] = v
                except Exception:
                    pass
            key = api_key or get_secret("OPENAI_APIKEY") or os.getenv("OPENAI_API_KEY")
        self.client = OpenAI(api_key=key)

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o-mini",
        **kwargs: Any,
    ) -> str:
        """
        - responses-only 모델(gpt-5-*, o1-*, 등)을 지원하기 위해 v1/responses를 우선 사용
        - 일부 구형/특수 모델에서 responses가 안 되면 chat.completions로 fallback
        """

        # 1) messages -> responses input 변환
        # responses API는 "input"에 role/content 구조를 그대로 넣을 수 있습니다.
        # (SDK 버전에 따라 shape가 조금 다를 수 있어, 가장 범용적인 형태로 작성)
        input_payload = [{"role": m["role"], "content": m["content"]} for m in messages]

        # kwargs 매핑 (responses에서 자주 쓰는 것만 안전하게 통과)
        # NOTE: max_tokens vs max_output_tokens 등 모델/SDK에 따라 다를 수 있어 둘 다 지원
        resp_kwargs = dict(kwargs)
        if "max_tokens" in resp_kwargs and "max_output_tokens" not in resp_kwargs:
            resp_kwargs["max_output_tokens"] = int(resp_kwargs.pop("max_tokens"))

        # Structured outputs:
        # - chat.completions: response_format=...
        # - responses: text={"format": response_format}
        response_format = resp_kwargs.pop("response_format", None)
        if response_format is not None:
            # Prefer responses API; some newer models are responses-only.
            try:
                text_cfg = resp_kwargs.get("text")
                if isinstance(text_cfg, dict):
                    text_cfg = dict(text_cfg)
                    text_cfg["format"] = response_format
                else:
                    text_cfg = {"format": response_format}
                resp = self.client.responses.create(
                    model=model,
                    input=input_payload,
                    text=text_cfg,
                    **{k: v for k, v in resp_kwargs.items() if k != "text"},
                )
                out = _extract_response_text(resp)
                if out:
                    return out
                return str(resp)
            except Exception:
                # Fall back to chat.completions if responses structured output is unavailable.
                try:
                    chat_kwargs = dict(resp_kwargs)
                    if "max_tokens" in chat_kwargs:
                        chat_kwargs["max_completion_tokens"] = int(chat_kwargs.pop("max_tokens"))
                    if "max_output_tokens" in chat_kwargs and "max_completion_tokens" not in chat_kwargs:
                        chat_kwargs["max_completion_tokens"] = int(chat_kwargs.pop("max_output_tokens"))
                    chat_kwargs.pop("reasoning", None)
                    response = self.client.chat.completions.create(
                        model=model, messages=messages, response_format=response_format, **chat_kwargs
                    )
                    message = response.choices[0].message
                    content = getattr(message, "content", None)
                    if content:
                        return content
                    parsed = getattr(message, "parsed", None)
                    if parsed is not None:
                        return json.dumps(parsed, ensure_ascii=False)
                    raise RuntimeError("Chat completion returned empty content for structured output.")
                except Exception:
                    # Give up on strict formatting and continue without response_format.
                    response_format = None

        try:
            resp_create_kwargs = dict(resp_kwargs)
            # Only pass `text` when the caller provided it; `None` can be rejected by the SDK.
            if "text" in resp_create_kwargs and resp_create_kwargs["text"] is None:
                resp_create_kwargs.pop("text", None)
            resp = self.client.responses.create(
                model=model,
                input=input_payload,
                **resp_create_kwargs,
            )
            # 최신 SDK는 output_text를 제공
            if hasattr(resp, "output_text") and resp.output_text:
                return resp.output_text
            # fallback: output 구조에서 텍스트 합치기
            if hasattr(resp, "output") and resp.output:
                texts = []
                for item in resp.output:
                    # item.content: [{"type":"output_text","text":"..."}] 같은 형태가 흔함
                    for c in getattr(item, "content", []) or []:
                        t = getattr(c, "text", None)
                        if t:
                            texts.append(t)
                if texts:
                    return "\n".join(texts)
            return str(resp)

        except Exception as e:
            # 2) responses 실패 시 chat.completions fallback (chat 전용 모델 호환)
            # (단, 현재 에러는 "chat 불가"였으므로 보통은 여기로 오지 않게 해야 함)
            try:
                chat_kwargs = dict(kwargs)
                chat_kwargs.pop("reasoning", None)
                if "max_tokens" in chat_kwargs:
                    chat_kwargs["max_completion_tokens"] = int(chat_kwargs.pop("max_tokens"))
                if "max_output_tokens" in chat_kwargs and "max_completion_tokens" not in chat_kwargs:
                    chat_kwargs["max_completion_tokens"] = int(chat_kwargs.pop("max_output_tokens"))
                # response_format is only used on chat path when explicitly requested above.
                chat_kwargs.pop("response_format", None)
                response = self.client.chat.completions.create(model=model, messages=messages, **chat_kwargs)
                message = response.choices[0].message
                content = getattr(message, "content", None)
                if content:
                    return content
                parsed = getattr(message, "parsed", None)
                if parsed is not None:
                    return json.dumps(parsed, ensure_ascii=False)
                raise RuntimeError("Chat completion returned empty content.")
            except Exception:
                raise e


class GrokProvider(BaseLLMProvider):
    """
    xAI 공식 튜토리얼의 xai-sdk 방식:
      - pip install xai-sdk
      - from xai_sdk import Client
      - from xai_sdk.chat import user, system
      - chat = client.chat.create(model="grok-4")
      - chat.append(...)
      - resp = chat.sample()
      - resp.content
     [oai_citation:3‡xAI](https://docs.x.ai/docs/tutorial)
    """

    def __init__(self, api_key: Optional[str] = None, timeout: int = 3600, store_messages: bool = False):
        try:
            from xai_sdk import Client  # type: ignore
        except Exception as e:
            raise RuntimeError("xai-sdk is not installed. Run: pip install xai-sdk") from e

        self.api_key = get_secret("GROK_APIKEY") if api_key is None else api_key
        if not self.api_key:
            raise RuntimeError("Grok API key is empty. Set XAI_API_KEY env or provide GROK_APIKEY/config.")

        self.client = Client(api_key=self.api_key, timeout=timeout)
        self.store_messages = store_messages

        # message builders
        from xai_sdk.chat import user as x_user  # type: ignore
        from xai_sdk.chat import system as x_system  # type: ignore

        self._x_user = x_user
        self._x_system = x_system

    def chat(self, messages: List[Dict[str, str]], model: Optional[str] = None, **kwargs) -> str:
        model_name = model or "grok-4"

        # xai-sdk는 "채팅 세션" 생성 후 append하는 형태
        chat = self.client.chat.create(
            model=model_name,
            store_messages=bool(kwargs.pop("store_messages", self.store_messages)),
        )

        # system은 여러 개면 합치거나, 첫 system만 쓰는게 안전
        # 여기선 "system들은 순서대로 append" (튜토리얼 패턴 유지)
        for m in messages:
            role = (m.get("role") or "user").strip()
            content = m.get("content") or ""
            if role == "system":
                chat.append(self._x_system(content))
            elif role == "user":
                chat.append(self._x_user(content))
            elif role == "assistant":
                # xai-sdk는 response 객체를 append하는 예시가 있으나,
                # 여기서는 간단히 'user'로 흡수(대부분의 데이터 생성엔 충분)
                # 필요하면 xai_sdk.chat.assistant 가 있는지 확인 후 분기하세요.
                chat.append(self._x_user(content))
            else:
                chat.append(self._x_user(content))

        # 샘플링 옵션: 튜토리얼은 sample()만 사용
        # (temperature 등은 SDK 옵션 지원 여부가 모델/SDK 버전에 따라 다를 수 있어 kwargs는 보수적으로 무시)
        resp = chat.sample()
        # 튜토리얼 기준 response.content 출력  [oai_citation:4‡xAI](https://docs.x.ai/docs/tutorial)
        return getattr(resp, "content", str(resp))


def build_llm_caller(platform: str) -> LLMCaller:
    if platform == "grok":
        provider = LLMProvider.GROK
    elif platform == "openai":
        provider = LLMProvider.OPENAI
    else:
        raise ValueError(f"Unsupported LLM provider: {platform!r} (expected 'openai' or 'grok')")
    if provider == LLMProvider.OPENAI:
        impl = OpenAIProvider()
    elif provider == LLMProvider.GROK:
        impl = GrokProvider()
    else:
        raise ValueError(f"Unsupported provider: {provider}")
    return LLMCaller(provider=provider, provider_impl=impl)


def _extract_response_text(resp: object) -> str:
    if hasattr(resp, "output_text") and resp.output_text:
        return resp.output_text
    if hasattr(resp, "output") and resp.output:
        texts: List[str] = []
        for item in resp.output:
            for c in getattr(item, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    texts.append(t)
        if texts:
            return "\n".join(texts)
    return ""


def fetch_x_search_context(
    keywords: List[str],
    days: int,
    limit: int,
    model: str = "grok-4-1-fast-reasoning",
    api_key: Optional[str] = None,
) -> str:
    key = api_key or os.getenv("XAI_API_KEY")
    if not key:
        return ""
    client = OpenAI(api_key=key, base_url="https://api.x.ai/v1")
    to_date = datetime.utcnow().date()
    from_date = to_date - timedelta(days=max(1, days))
    snippets: List[str] = []
    for kw in keywords:
        prompt = (
            f"Search X for recent reports related to: {kw}. "
            f"Return up to {limit} short bullet points with key claims. "
            "If dates or handles are available, include them."
        )
        try:
            resp = client.responses.create(
                model=model,
                input=[{"role": "user", "content": prompt}],
                tools=[
                    {
                        "type": "x_search",
                        "from_date": from_date.isoformat(),
                        "to_date": to_date.isoformat(),
                    }
                ],
            )
        except Exception:
            continue
        text = _extract_response_text(resp)
        if text:
            snippets.append(f"[{kw}] {text.strip()}")
    if not snippets:
        return ""
    return "X_SEARCH\n" + "\n\n".join(snippets)
