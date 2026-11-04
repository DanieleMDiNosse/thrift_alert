"""Use Hermes' own OAuth client in its own interpreter, without agent execution.

Only an operator may configure executable/profile paths. Never expose them through
HTTP settings. Inference locking, domain validation and retry remain in llm.py.
"""

import json
import os
from pathlib import Path
import subprocess

from .llm import InferenceError, InferenceUnavailable


def complete(payload, config):
    if "tools" in payload or "tool_choice" in payload:
        raise InferenceError(code="http_rejected")
    if config.configuration_error():
        raise InferenceUnavailable(code="hermes_runtime")
    request = dict(
        payload, timeout=config.timeout, max_response_bytes=config.max_response_bytes
    )
    # Do not pass application secrets, inherited Python paths, or alternate API
    # routes. Hermes resolves/refreshes only the selected profile's Codex login.
    env = {
        key: os.environ[key]
        for key in (
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "XDG_CONFIG_HOME",
            "XDG_CACHE_HOME",
            "TMPDIR",
        )
        if key in os.environ
    }
    env["HERMES_HOME"] = config.hermes_home
    runner = Path(__file__).with_name("hermes_runner.py")
    try:
        result = subprocess.run(
            [config.hermes_python, "-I", str(runner)],
            input=json.dumps(request, ensure_ascii=False, allow_nan=False).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=config.timeout,
        )
    except subprocess.TimeoutExpired:
        raise InferenceUnavailable(code="timeout") from None
    except OSError:
        raise InferenceUnavailable(code="hermes_runtime") from None
    if len(result.stdout) > config.max_response_bytes:
        raise InferenceError(code="response_too_large")
    try:
        body = json.loads(result.stdout)
        if not isinstance(body, dict):
            raise ValueError
    except (ValueError, TypeError):
        raise InferenceError(code="invalid_response") from None
    code = body.get("error")
    if code or result.returncode:
        if code in ("missing_credentials", "auth_rejected"):
            raise InferenceUnavailable(code="hermes_auth")
        if code in ("model_offline", "timeout", "hermes_runtime"):
            raise InferenceUnavailable(code=code)
        raise InferenceError(
            code=code
            if code
            in (
                "http_rejected",
                "output_truncated",
                "response_too_large",
                "empty_response",
                "invalid_response",
            )
            else "inference_error"
        )
    content = body.get("content")
    if not isinstance(content, str) or not content:
        raise InferenceError(code="empty_response")
    # Keep the existing validation path identical for API-key and OAuth backends.
    return json.dumps(
        {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    ).encode()
