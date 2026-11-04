"""Isolated JSON-only Codex OAuth runner. Not an agent, daemon, or tool server.

Executed by the configured Hermes interpreter with -I. No application imports or
plugins; Hermes owns credential resolution/refresh. Never serialize exceptions.
"""

import contextlib
import json
import logging
import sys


class RunnerError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _stream_error_code(event):
    """Classify known SDK/dict error fields, never inspect or echo free text."""

    def field(value, name):
        return (
            value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        )

    response = field(event, "response")
    codes = [
        field(value, "code")
        for value in (
            event,
            field(event, "error"),
            field(response, "error"),
        )
    ]
    for code in codes:
        if not isinstance(code, str):
            continue
        if code in {
            "invalid_api_key",
            "authentication_error",
            "invalid_authentication",
            "invalid_token",
            "token_expired",
            "unauthorized",
        }:
            return "auth_rejected"
        if code in {
            "rate_limit_exceeded",
            "server_error",
            "overloaded_error",
            "temporarily_unavailable",
        }:
            return "model_offline"
    return "http_rejected"


def generate(request, resolve):
    if not isinstance(request, dict) or "tools" in request or "tool_choice" in request:
        raise RunnerError("http_rejected")
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise RunnerError("http_rejected")
    instructions, inputs = [], []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if role not in ("system", "user", "assistant") or not isinstance(content, str):
            raise RunnerError("http_rejected")
        if role == "system":
            instructions.append(content)
        else:
            inputs.append({"role": role, "content": content})
    if not inputs:
        raise RunnerError("http_rejected")
    model = request["model"]
    timeout = request["timeout"]
    limit = request["max_response_bytes"]
    if (
        not isinstance(model, str)
        or not model
        or not 1 <= timeout <= 300
        or not 1024 <= limit <= 4000000
    ):
        raise RunnerError("http_rejected")
    format_ = request["response_format"]
    if format_.get("type") == "json_schema":
        spec = format_["json_schema"]
        text = {
            "format": {
                "type": "json_schema",
                "name": spec["name"],
                "strict": True,
                "schema": spec["schema"],
            }
        }
    elif format_.get("type") == "json_object":
        text = {"format": {"type": "json_object"}}
    else:
        raise RunnerError("http_rejected")
    client, resolved_model = resolve("openai-codex", model=model, raw_codex=True)
    if client is None:
        raise RunnerError("missing_credentials")
    client = client.with_options(max_retries=0, timeout=timeout)
    try:
        # Codex does not accept max_output_tokens. Enforce a wall deadline in the
        # parent and an output-byte bound here; never claim a provider token cap.
        events = client.responses.create(
            model=resolved_model,
            instructions="\n\n".join(instructions),
            input=inputs,
            store=False,
            stream=True,
            reasoning={"effort": request["reasoning_effort"]},
            text=text,
        )
        chunks, size, completed = [], 0, False
        with events:
            for event in events:
                kind = event.type
                if kind == "response.output_text.delta":
                    size += len(event.delta.encode("utf-8"))
                    if size > limit - 256:
                        raise RunnerError("response_too_large")
                    chunks.append(event.delta)
                elif kind == "response.incomplete":
                    raise RunnerError("output_truncated")
                elif kind in ("error", "response.failed"):
                    raise RunnerError(_stream_error_code(event))
                elif kind == "response.refusal.delta":
                    raise RunnerError("http_rejected")
                elif kind == "response.completed":
                    completed = True
        if not completed:
            raise RunnerError("invalid_response")
        content = "".join(chunks)
        if not content.strip():
            raise RunnerError("empty_response")
        return content
    finally:
        close = getattr(client, "close", None)
        if close:
            close()


def main():
    logging.disable(logging.CRITICAL)
    result = None
    request = {}
    try:
        raw = sys.stdin.buffer.read(1000001)
        if len(raw) > 1000000:
            raise RunnerError("http_rejected")
        request = json.loads(raw)
        # Library diagnostics must never become protocol output. Parent discards
        # stderr so OAuth/provider exception bodies cannot enter application logs.
        with contextlib.redirect_stdout(sys.stderr):
            from agent.auxiliary_client import resolve_provider_client

            content = generate(request, resolve_provider_client)
        result = {"content": content}
    except RunnerError as error:
        result = {"error": error.code}
    except ModuleNotFoundError:
        result = {"error": "hermes_runtime"}
    except Exception as error:
        status = getattr(error, "status_code", None)
        if status in (401, 403):
            code = "auth_rejected"
        elif status in (429, 500, 502, 503, 504):
            code = "model_offline"
        elif (
            isinstance(error, TimeoutError) or type(error).__name__ == "APITimeoutError"
        ):
            code = "timeout"
        elif type(error).__name__ == "APIConnectionError":
            code = "model_offline"
        else:
            code = "http_rejected" if status else "invalid_response"
        result = {"error": code}
    encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
    # JSON escaping itself may expand output. Bound the actual protocol too.
    limit = (
        request.get("max_response_bytes", 1000000)
        if isinstance(locals().get("request"), dict)
        else 1000000
    )
    if len(encoded) > limit:
        encoded = b'{"error":"response_too_large"}'
    sys.stdout.buffer.write(encoded)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())
