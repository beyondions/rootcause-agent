import os
import time

from dotenv import load_dotenv
from groq import APIConnectionError, BadRequestError, InternalServerError, RateLimitError

load_dotenv()

DEFAULT_MODELS = "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b"
MODELS = [m.strip() for m in os.getenv("MODELS", DEFAULT_MODELS).split(",") if m.strip()]
RETRIES = 4

_exhausted = set()


class QuotaExhausted(RuntimeError):
    pass


def current_model():
    for model in MODELS:
        if model not in _exhausted:
            return model
    raise QuotaExhausted(f"daily Groq quota used up for all models: {', '.join(MODELS)}")


def _is_daily_limit(error):
    return isinstance(error, RateLimitError) and "per day" in str(error)


def _retryable(error):
    if isinstance(error, (RateLimitError, APIConnectionError, InternalServerError)):
        return True
    return isinstance(error, BadRequestError) and any(
        code in str(error) for code in ("tool_use_failed", "output_parse_failed"))


def _wait_seconds(error, attempt):
    if isinstance(error, RateLimitError):
        try:
            return float(error.response.headers.get("retry-after"))
        except (TypeError, ValueError):
            return 10 * (attempt + 1)
    return 2 * (attempt + 1)


def _chat_one(client, model, retries, reasoning_effort, kwargs):
    if reasoning_effort and model.startswith("openai/gpt-oss"):
        kwargs = {**kwargs, "reasoning_effort": reasoning_effort}
    for attempt in range(retries):
        try:
            return client.chat.completions.create(model=model, **kwargs)
        except Exception as e:
            if _is_daily_limit(e):
                raise QuotaExhausted(f"daily Groq quota used up for {model}") from e
            if not _retryable(e):
                raise
            if attempt == retries - 1:
                raise RuntimeError(f"Groq failed {retries} times ({type(e).__name__}): "
                                   "try again in a minute") from e
            wait = _wait_seconds(e, attempt)
            print(f"  (Groq {type(e).__name__}, retrying in {wait:.0f}s)")
            time.sleep(wait)


def chat(client, retries=RETRIES, reasoning_effort=None, **kwargs):
    while True:
        model = current_model()
        try:
            return _chat_one(client, model, retries, reasoning_effort, kwargs)
        except QuotaExhausted as e:
            _exhausted.add(model)
            print(f"  ({e}; switching model)")
