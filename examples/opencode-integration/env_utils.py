# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import shlex
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

DEFAULT_OPENCODE_DIR = "/root/.pi/agent"
DEFAULT_SESSION_DIR = f"{DEFAULT_OPENCODE_DIR}/sessions"
DEFAULT_WORKSPACE = "/workspace"

PROVIDER_KEY_ENV = {
    "moonshot": "MOONSHOT_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

PROVIDER_DEFAULT_HOST = {
    "moonshot": "api.moonshot.cn",
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "deepseek": "api.deepseek.com",
}

# Only Moonshot ships a default model; other providers must set OPENCODE_MODEL
# explicitly (model IDs are provider-specific and change often).
PROVIDER_DEFAULT_MODEL = {
    "moonshot": "kimi-latest",
}

PASSTHROUGH_ENV_NAMES = (
    "MOONSHOT_BASE_URL",
    "OPENAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "ANTHROPIC_MODEL",
    "OPENCODE_CACHE_RETENTION",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
)


def load_local_dotenv() -> None:
    """Best-effort load of a nearby .env file without overriding real env vars."""
    candidate_paths = [
        Path(__file__).with_name(".env"),
        Path.cwd() / ".env",
    ]

    seen_paths: set[Path] = set()
    for path in candidate_paths:
        resolved_path = path.resolve()
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)

        if path.is_file():
            load_dotenv(dotenv_path=path, override=False)
            return


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def optional(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer, got {raw!r}") from exc


def opencode_provider() -> str:
    # Normalize case so every downstream comparison and dict lookup
    # (provider_inject, PROVIDER_DEFAULT_HOST, key candidates, ...) is
    # case-insensitive; "Moonshot" and "moonshot" must behave the same.
    return optional("OPENCODE_PROVIDER", "moonshot").strip().lower()


def opencode_model() -> str:
    provider = opencode_provider()
    explicit = os.environ.get("OPENCODE_MODEL")
    if not explicit and provider == "anthropic":
        explicit = os.environ.get("ANTHROPIC_MODEL")
    if explicit:
        return explicit
    default = PROVIDER_DEFAULT_MODEL.get(provider)
    if default:
        return default
    raise SystemExit(
        f"No default model for provider {provider!r}. Set OPENCODE_MODEL in your .env "
        "(model IDs are provider-specific; there is no safe cross-provider default)."
    )


def opencode_workspace() -> str:
    return optional("OPENCODE_WORKSPACE", DEFAULT_WORKSPACE)


def opencode_cli() -> str:
    # The ``opencode-ai`` npm package exposes the CLI as ``opencode``.
    # ``OPENCODE_CLI`` is provided as an escape hatch for custom installs or
    # wrapper scripts that expose a different binary name.
    return optional("OPENCODE_CLI", "opencode")


def provider_key_name(provider: str | None = None) -> str:
    provider_name = provider or opencode_provider()
    names = provider_key_candidates(provider_name)
    for name in names:
        if os.environ.get(name):
            return name
    return names[0]


def require_provider_key(provider: str | None = None) -> str:
    provider_name = provider or opencode_provider()
    names = provider_key_candidates(provider_name)
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise SystemExit(
        "Missing required environment variable: one of " + ", ".join(names)
    )


def provider_key_candidates(provider: str) -> tuple[str, ...]:
    provider = provider.strip().lower()
    default_name = PROVIDER_KEY_ENV.get(provider, f"{provider.upper()}_API_KEY")
    return (default_name,)


def build_opencode_env(include_secrets: bool = True) -> dict[str, str]:
    """Build the env map passed to the OpenCode command inside the sandbox.

    Set ``include_secrets=False`` for the CubeEgress vault flavor: the real
    provider key rides the wire via egress injection, so it must never enter
    the sandbox environment.
    """
    env = {
        "OPENCODE_AGENT_DIR": optional("OPENCODE_AGENT_DIR", DEFAULT_OPENCODE_DIR),
        "OPENCODE_SESSION_DIR": optional(
            "OPENCODE_SESSION_DIR", DEFAULT_SESSION_DIR
        ),
        "OPENCODE_TELEMETRY": optional("OPENCODE_TELEMETRY", "0"),
    }
    for name in PASSTHROUGH_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            env[name] = value
    if include_secrets:
        # Forward ONLY the active provider's key(s), never every known secret —
        # a host with several provider keys (e.g. a CI matrix) must not leak all
        # of them into the sandbox.
        for name in provider_key_candidates(opencode_provider()):
            value = os.environ.get(name)
            if value:
                env[name] = value
    return env


def opencode_llm_host(provider: str | None = None) -> str:
    """Resolve the LLM API host that OpenCode must reach.

    Precedence: explicit ``OPENCODE_LLM_HOST`` > host parsed from the provider
    base URL (for OpenAI-compatible endpoints) > the provider default.
    """
    provider_name = (provider or opencode_provider()).strip().lower()
    explicit = os.environ.get("OPENCODE_LLM_HOST")
    if explicit:
        return _host_from_url(explicit)

    base_url_env = {
        "moonshot": "MOONSHOT_BASE_URL",
        "openai": "OPENAI_BASE_URL",
        "anthropic": "ANTHROPIC_BASE_URL",
        "deepseek": "DEEPSEEK_BASE_URL",
    }.get(provider_name)
    if base_url_env:
        base_url = os.environ.get(base_url_env)
        if base_url:
            host = _host_from_url(base_url)
            if host:
                return host
    return PROVIDER_DEFAULT_HOST.get(provider_name, "")


def _host_from_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        return ""
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    return urlparse(candidate).hostname or ""


def provider_inject(provider: str, secret: str) -> list[dict[str, str]]:
    """CubeEgress credential-injection specs for a provider's auth header(s).

    Each dict maps directly to a ``cubesandbox.Inject(header=..., secret=...,
    format=...)``. CubeEgress attaches these headers to matched outbound
    requests, so the real key rides the wire and never enters the sandbox VM.
    OpenAI-compatible providers (Moonshot, DeepSeek, OpenAI, ...) use
    ``Authorization: Bearer``.
    """
    return [{"header": "Authorization", "secret": secret, "format": "Bearer ${SECRET}"}]


def opencode_command(
    prompt: str, *, mode: str = "json", name: str | None = None, approve: bool = True
) -> str:
    """Build a headless (non-interactive) OpenCode invocation.

    ``--print`` makes OpenCode process the prompt and exit instead of launching
    the interactive TUI (which would hang over the E2B exec channel).
    ``--mode json`` streams machine-readable JSONL events. ``--approve`` trusts
    project-local files for this run — handy in an isolated sandbox, but pass
    ``approve=False`` in high-security workflows where those files could be
    attacker-controlled. The prompt is passed as the trailing positional
    message.
    """
    args = [opencode_cli(), "--print"]
    if mode:
        args.extend(["--mode", mode])
    provider = opencode_provider()
    model = opencode_model()
    thinking = optional("OPENCODE_THINKING")
    if provider:
        args.extend(["--provider", provider])
    if model:
        args.extend(["--model", model])
    if thinking:
        args.extend(["--thinking", thinking])
    if name:
        args.extend(["--name", name])
    if approve:
        args.append("--approve")
    args.append(prompt)
    return " ".join(shlex.quote(arg) for arg in args)


def shell_join(*parts: str) -> str:
    return " && ".join(part for part in parts if part)
