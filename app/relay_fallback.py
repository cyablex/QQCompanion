"""Recognize explicit relay filtering without classifying conversational refusals."""

import re


def relay_blocked(text):
    if not isinstance(text, str):
        return False
    text = re.sub(r"\s+", "", text)
    return bool(
        re.fullmatch(
            r"(?:抱歉[，,。]*)?系统检测到[您你]当前输入的信息存在敏感内容[，,。]*"
            r"我无法响应[您你]的请求[，,。]*请检查后重新输入[。.!！]*",
            text,
        )
    )


def relay_filter_error(exc):
    body = getattr(exc, "body", None)
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    if not isinstance(error, dict):
        return False
    return any(
        isinstance(error.get(key), str)
        and error.get(key)
        in {
            "content_filter",
            "content_policy_violation",
            "moderation_blocked",
            "sensitive_content",
        }
        for key in ("code", "type")
    )


def relay_connection_error(exc):
    # Only provider transport errors, not arbitrary application failures.
    return type(exc).__name__ in {'APIConnectionError', 'APITimeoutError'}


def transport_diagnostic(exc):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 6:
        seen.add(id(exc))
        chain.append(type(exc).__name__)
        exc = exc.__cause__ or exc.__context__
    return chain
