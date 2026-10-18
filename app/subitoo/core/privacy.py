"""Best-effort diagnostic redaction; never log raw network exceptions/tracebacks.

Not a DLP boundary: untrusted plugins and arbitrary raw payloads remain trusted-local
inputs. Avoid storing response bodies in diagnostics where possible.
"""
import os
import re
from dotenv import dotenv_values


def safe_error(error: BaseException) -> str:
    text = str(error)
    values = {**dotenv_values('.env'), **os.environ}
    for key, value in values.items():
        if value and len(value) >= 4 and any(part in key.upper() for part in
                ('TOKEN', 'PASSWORD', 'SECRET', 'API_KEY', 'TOPIC', 'PROXY_URL', 'COOKIE')):
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)
    text = re.sub(r'(?i)((?:access_token|token|api_key|password|cookie)=)[^\s&]+',
                  r'\1[REDACTED]', text)
    return f'{type(error).__name__}: {text[:400]}'
