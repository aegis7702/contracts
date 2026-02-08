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
        - Prefer v1/responses to support responses-only models (gpt-5-*, o1-*, etc.)
        - If the responses API is unavailable for a given model/SDK, fall back to chat.completions
        """

        # 1) Convert messages -> responses input payload.
        # The responses API can accept the same role/content structure under `input`.
        # (SDK shapes vary slightly; keep this as generic as possible.)
        input_payload = [{"role": m["role"], "content": m["content"]} for m in messages]

        # Map kwargs (only pass through commonly supported fields for the responses API).
        # NOTE: token limit field names vary by model/SDK (`max_tokens` vs `max_output_tokens`), so support both.
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
            # Newer SDKs provide `output_text`.
            if hasattr(resp, "output_text") and resp.output_text:
                return resp.output_text
            # Fallback: stitch together text from the `output` structure.
            if hasattr(resp, "output") and resp.output:
                texts = []
                for item in resp.output:
                    # `item.content` often looks like: [{"type":"output_text","text":"..."}].
                    for c in getattr(item, "content", []) or []:
                        t = getattr(c, "text", None)
                        if t:
                            texts.append(t)
                if texts:
                    return "\n".join(texts)
            return str(resp)

        except Exception as e:
            # 2) If responses fails, fall back to chat.completions (for chat-only models).
            # (If the root error was "chat not supported", we should not usually land here.)
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
    Based on the official xAI xai-sdk tutorial:
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

        # xai-sdk creates a chat session, then you append messages.
        chat = self.client.chat.create(
            model=model_name,
            store_messages=bool(kwargs.pop("store_messages", self.store_messages)),
        )

        # If there are multiple system messages, it is safest to merge them or use only the first.
        # Here we append system messages in order (keep the tutorial pattern).
        for m in messages:
            role = (m.get("role") or "user").strip()
            content = m.get("content") or ""
            if role == "system":
                chat.append(self._x_system(content))
            elif role == "user":
                chat.append(self._x_user(content))
            elif role == "assistant":
                # The xai-sdk examples sometimes append assistant/response objects, but
                # here we simply treat it as user content (good enough for most PoC data-generation).
                # If needed, detect whether xai_sdk.chat.assistant exists and branch accordingly.
                chat.append(self._x_user(content))
            else:
                chat.append(self._x_user(content))

        # Sampling: the tutorial uses sample() only.
        # (Options like temperature may vary by model/SDK; we conservatively ignore unsupported kwargs.)
        resp = chat.sample()
        # Tutorial output: resp.content  [oai_citation:4‡xAI](https://docs.x.ai/docs/tutorial)
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
