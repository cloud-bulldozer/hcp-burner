#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Helpers to redact sensitive values before logging."""
import re
from urllib.parse import urlparse

_SENSITIVE_FLAGS = frozenset({
    "--token", "--password", "--client-secret", "-p",
})
_SENSITIVE_FLAG_PREFIXES = ("--token=", "--password=", "--client-secret=")
_SENSITIVE_KEY_FRAGMENTS = ("password", "secret", "token", "kubeconfig", "credential")
_URL_AUTH_PATTERN = re.compile(r"https://[^:@/]+:[^@/]+@")


def redact_command(command):
    if isinstance(command, (list, tuple)):
        return _redact_command_list(command)
    return _redact_command_string(str(command))


def _redact_command_list(cmd):
    result = []
    i = 0
    while i < len(cmd):
        item = str(cmd[i])
        if any(item.startswith(prefix) for prefix in _SENSITIVE_FLAG_PREFIXES):
            key = item.split("=", 1)[0]
            result.append(f"{key}=***")
            i += 1
            continue
        if item in _SENSITIVE_FLAGS:
            result.append(item)
            if i + 1 < len(cmd):
                result.append("***")
                i += 2
            else:
                i += 1
            continue
        result.append(_redact_command_string(item))
        i += 1
    return result


def _redact_command_string(text):
    text = _URL_AUTH_PATTERN.sub("https://***:***@", text)
    text = re.sub(r"(--token=)\S+", r"\1***", text)
    text = re.sub(r"(--password=)\S+", r"\1***", text)
    text = re.sub(r"(--client-secret=)\S+", r"\1***", text)
    text = re.sub(r"(--token)\s+\S+", r"\1 ***", text)
    text = re.sub(r"(--password)\s+\S+", r"\1 ***", text)
    text = re.sub(r"(--client-secret)\s+\S+", r"\1 ***", text)
    return text


def redact_output(text):
    if text is None or text == "":
        return text
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = _URL_AUTH_PATTERN.sub("https://***:***@", text)
    lowered = text.lower()
    if "kubeconfig" in lowered and any(marker in text for marker in ('"data"', "apiVersion", "clusters")):
        return "<redacted: sensitive kubeconfig/secret content>"
    return text


def redact_url(url):
    if not url:
        return url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def redact_metadata(metadata):
    if isinstance(metadata, dict):
        return {
            key: ("***" if _is_sensitive_key(key) else redact_metadata(value))
            for key, value in metadata.items()
        }
    if isinstance(metadata, list):
        return [redact_metadata(item) for item in metadata]
    return metadata


def _is_sensitive_key(key):
    lowered = str(key).lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)
