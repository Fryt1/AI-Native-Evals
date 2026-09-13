"""Provider profiles, live model discovery, and reasoning-level authority.

A Provider owns *where* a run's LLM traffic goes: an env file holding the
upstream base URL plus credential, and the set of models that endpoint actually
advertises. A model binding owns *what* is requested: the model id, wire
protocol and reasoning effort.

Nothing here guesses. Which models a user may select comes from the provider's
own ``/v1/models`` response, and which reasoning levels are legal comes from the
provider's own rejection of an unknown one. The static lists in
``profiles/providers/*.yaml`` are fallbacks for an endpoint that cannot be
interrogated, never the primary authority. A hand-maintained model list drifts
-- that is exactly how this repository came to hold a binding for a model
nobody serves.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

# No discovery call may block a plan preview indefinitely.
DEFAULT_TIMEOUT_SECONDS = 20

# The relay answers /v1/models quickly, so a short cache keeps a UI session
# responsive without serving a stale menu across a provider change.
CACHE_TTL_SECONDS = 30.0

# The reasoning vocabulary each Agent can actually put on the wire. A requested
# level must lie in the intersection of this set and the provider's own enum,
# because both sides reject values they do not recognize.
AGENT_REASONING_LEVELS: dict[str, tuple[str, ...]] = {
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
    "dsh-acp": ("off", "low", "high", "max"),
}

# Which wire protocols each Agent can actually speak. Codex renders either
# protocol into its config; DSH's built-in DeepSeek route is chat-completions
# only, so a responses-only provider cannot serve a DSH run. pi declares its
# gateway route as `openai-completions`. Both sides must agree on one protocol,
# and neither side gets to assume it.
AGENT_WIRE_APIS: dict[str, tuple[str, ...]] = {
    "codex": ("responses", "chat"),
    "dsh-acp": ("chat",),
    "pi": ("chat",),
}

# Providers and Agents spell the "no extra reasoning" level differently.
_LEVEL_ALIASES = {"none": "off", "off": "off", "": "off"}

_SUPPORTED_VALUES_RE = re.compile(r"supported values are:\s*(.+?)\.", re.IGNORECASE)
_QUOTED_RE = re.compile(r"'([^']+)'")

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[float, Any]] = {}


class ProviderError(ValueError):
    """Raised when a provider profile is malformed or cannot be used."""


def _cache_get(key: tuple[str, str]) -> Any:
    with _cache_lock:
        entry = _cache.get(key)
    if not entry:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
        return None
    return value


def _cache_put(key: tuple[str, str], value: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), value)


def clear_cache() -> None:
    """Drop cached discovery results. Used by tests and profile reloads."""
    with _cache_lock:
        _cache.clear()


def load_provider_profiles(
    repo_root: Path, config: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Load every ``profiles/providers/*.yaml`` file keyed by provider id."""
    profile_roots = config.get("profile_roots")
    raw_root: Any = "profiles/providers"
    if isinstance(profile_roots, Mapping) and profile_roots.get("providers"):
        raw_root = profile_roots["providers"]
    root = Path(str(raw_root))
    if not root.is_absolute():
        root = repo_root / root
    profiles: dict[str, dict[str, Any]] = {}
    if not root.is_dir():
        return profiles
    for path in sorted(root.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ProviderError(f"could not read provider profile {path.name}: {exc}") from exc
        if not isinstance(data, Mapping):
            raise ProviderError(f"provider profile {path.name} must be a YAML object")
        provider_id = str(data.get("id") or path.stem).strip()
        if not provider_id:
            raise ProviderError(f"provider profile {path.name} has an empty id")
        profile = dict(data)
        profile["id"] = provider_id
        profile["source_path"] = str(path)
        profiles[provider_id] = profile
    return profiles


def provider_env_file(repo_root: Path, profile: Mapping[str, Any]) -> Path | None:
    """Resolve a provider's env file, or ``None`` when it declares none."""
    raw = profile.get("env_file")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def read_provider_env(path: Path) -> dict[str, str]:
    """Read ``KEY=value`` pairs from a provider env file."""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProviderError(f"could not read provider env file {path}: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def upstream_base_url(env: Mapping[str, str]) -> str | None:
    """The provider's API base, accepting both this repo's and gateway spellings."""
    raw = env.get("UPSTREAM_BASE_URL") or env.get("AI_NATIVE_EVALS_LLM_BASE_URL")
    return raw.rstrip("/") if raw else None


def upstream_api_key(env: Mapping[str, str]) -> str | None:
    """The provider credential, accepting both this repo's and gateway spellings."""
    return env.get("UPSTREAM_API_KEY") or env.get("AI_NATIVE_EVALS_LLM_API_KEY")


def _request(
    url: str,
    key: str | None,
    *,
    payload: Mapping[str, Any] | None,
    timeout: float,
) -> tuple[int, Any]:
    """Return ``(status, decoded_body)``; HTTP errors are decoded, not raised."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            try:
                return response.status, json.loads(body)
            except json.JSONDecodeError:
                return response.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise ProviderError(f"could not reach {url}: {exc}") from exc


def _model_ids(body: Any) -> list[str]:
    """Read model ids out of any list shape an OpenAI-compatible endpoint uses."""
    entries: Any = None
    if isinstance(body, Mapping):
        for key in ("data", "models"):
            value = body.get(key)
            if isinstance(value, list):
                entries = value
                break
            if isinstance(value, Mapping):
                return [str(name) for name in value]
    if not isinstance(entries, list):
        return []
    ids: list[str] = []
    for entry in entries:
        if isinstance(entry, Mapping):
            value = entry.get("id") or entry.get("name") or entry.get("model")
        else:
            value = entry
        if value:
            ids.append(str(value))
    return ids


def _message_of(body: Any) -> str | None:
    if not isinstance(body, Mapping):
        return None
    error = body.get("error")
    if isinstance(error, Mapping):
        message = error.get("message")
        return message if isinstance(message, str) else None
    if isinstance(error, str):
        return error
    message = body.get("message")
    return message if isinstance(message, str) else None


def discover_models(
    profile: Mapping[str, Any],
    env: Mapping[str, str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[list[str], str | None]:
    """Ask the provider which models it serves.

    Returns ``(model_ids, error)``. "Listed nothing" is reported as an error
    rather than falling back silently, because "no models" and "could not ask"
    must not look the same to someone choosing a model.
    """
    base = upstream_base_url(env)
    if not base:
        return [], "provider env file has no UPSTREAM_BASE_URL"
    cache_key = (str(profile.get("id") or ""), f"models:{base}")
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    try:
        status, body = _request(
            f"{base}/v1/models", upstream_api_key(env), payload=None, timeout=timeout
        )
    except ProviderError as exc:
        return [], str(exc)
    if status >= 400:
        detail = _message_of(body) or str(body)
        return [], f"GET /v1/models returned HTTP {status}: {detail}"[:300]
    ids = _model_ids(body)
    if not ids:
        return [], "GET /v1/models returned no readable model list"
    result = (ids, None)
    _cache_put(cache_key, result)
    return result


def discover_reasoning_levels(
    profile: Mapping[str, Any],
    env: Mapping[str, str],
    model: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[str] | None:
    """Ask the provider which reasoning levels it accepts for ``model``.

    The endpoint states its full enum when it rejects an unknown value, so one
    deliberately-invalid probe is the cheapest authoritative answer. Returns
    ``None`` when the endpoint does not answer in a readable way.
    """
    base = upstream_base_url(env)
    if not base:
        return None
    cache_key = (str(profile.get("id") or ""), f"reasoning:{base}:{model}")
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached or None
    payload = {
        "model": model,
        "input": "probe",
        "max_output_tokens": 16,
        "reasoning": {"effort": "__ai_native_evals_probe__"},
    }
    try:
        status, body = _request(
            f"{base}/v1/responses", upstream_api_key(env), payload=payload, timeout=timeout
        )
    except ProviderError:
        return None
    levels: list[str] = []
    if status >= 400:
        message = _message_of(body)
        if message:
            match = _SUPPORTED_VALUES_RE.search(message)
            if match:
                levels = [value.lower() for value in _QUOTED_RE.findall(match.group(1))]
    _cache_put(cache_key, levels)
    return levels or None


def fallback_reasoning_levels(profile: Mapping[str, Any]) -> list[str]:
    """Static levels declared by a provider profile."""
    raw = profile.get("reasoning_levels")
    if isinstance(raw, list):
        return [str(value) for value in raw]
    return []


def provider_wire_apis(profile: Mapping[str, Any]) -> list[str]:
    """Wire protocols a provider profile declares, in preference order."""
    raw = profile.get("wire_api")
    if isinstance(raw, list):
        return [str(value) for value in raw]
    if raw:
        return [str(raw)]
    return ["responses"]


def agent_wire_apis(agent: str, adapter: str | None = None) -> list[str]:
    """Wire protocols one Agent can transmit."""
    for key in (adapter, agent):
        if key and key in AGENT_WIRE_APIS:
            return list(AGENT_WIRE_APIS[str(key)])
    return list(AGENT_WIRE_APIS["codex"])


def resolve_wire_api(
    profile: Mapping[str, Any],
    agent: str,
    adapter: str | None = None,
) -> str | None:
    """Pick the protocol both the provider and the Agent support.

    Returns ``None`` when the two sets do not intersect, which is a real
    configuration error rather than something to paper over with a default.
    """
    agent_apis = {value.lower() for value in agent_wire_apis(agent, adapter)}
    for value in provider_wire_apis(profile):
        if value.lower() in agent_apis:
            return value
    return None


def agent_reasoning_levels(agent: str, adapter: str | None = None) -> list[str]:
    """The reasoning vocabulary one Agent can transmit."""
    for key in (adapter, agent):
        if key and key in AGENT_REASONING_LEVELS:
            return list(AGENT_REASONING_LEVELS[str(key)])
    return list(AGENT_REASONING_LEVELS["codex"])


def normalize_level(value: str) -> str:
    """Map spelling differences between providers and agents onto one name."""
    key = str(value).strip().lower()
    return _LEVEL_ALIASES.get(key, key)


def allowed_reasoning_levels(
    provider_levels: list[str],
    agent_levels: list[str],
) -> list[str]:
    """Levels both sides accept, spelled the way the Agent spells them.

    The result is written into the Agent's own configuration, so it must use
    the Agent's vocabulary: the relay says ``none`` where DSH says ``off``, and
    handing DSH the word ``none`` would be rejected by its own adapter. Order
    follows the provider so the menu reads naturally for that endpoint.
    """
    agent_spelling = {normalize_level(value): value for value in agent_levels}
    allowed: list[str] = []
    for value in provider_levels:
        canonical = normalize_level(value)
        if canonical not in agent_spelling:
            continue
        spelled = agent_spelling[canonical]
        if spelled not in allowed:
            allowed.append(spelled)
    return allowed


def provider_models(profile: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Declared per-model display metadata, keyed by model id."""
    result: dict[str, dict[str, Any]] = {}
    raw = profile.get("models")
    if not isinstance(raw, list):
        return result
    for entry in raw:
        if isinstance(entry, Mapping) and entry.get("id"):
            result[str(entry["id"])] = dict(entry)
        elif isinstance(entry, str):
            result[entry] = {"id": entry}
    return result


def provider_summary(
    repo_root: Path,
    profile: Mapping[str, Any],
    *,
    agent: str,
    adapter: str | None = None,
) -> dict[str, Any]:
    """Describe one provider without any network call.

    This is what a provider list renders from, so opening the dialog never
    waits on a remote endpoint.
    """
    provider_id = str(profile.get("id") or "")
    env_path = provider_env_file(repo_root, profile)
    configured = bool(env_path and env_path.is_file())
    declared = fallback_reasoning_levels(profile)
    return {
        "id": provider_id,
        "name": str(profile.get("name") or provider_id),
        "wire_api": provider_wire_apis(profile),
        "configured": configured,
        "default_reasoning": str(profile.get("default_reasoning") or ""),
        "declared_models": sorted(provider_models(profile)),
        "fallback_reasoning_levels": allowed_reasoning_levels(
            declared, agent_reasoning_levels(agent, adapter)
        ),
    }


def model_capability(model_id: str) -> str:
    """Classify a model as usable for an Agent task, or not.

    ``/v1/models`` answers "what can be called", not "what can hold a
    conversation and call tools". A relay commonly lists image and embedding
    models alongside chat models, and picking one produces a run that fails
    after the container has already started.

    The classification is deliberately narrow. Anything not recognised as a
    non-chat model stays ``chat``: wrongly hiding a working model would be worse
    than showing a suspicious one, because the operator can always see the id.
    """
    lowered = model_id.lower()
    # Image generation/editing models cannot act as an Agent.
    image_markers = ("-image", "image-", "dall-e", "stable-diffusion", "flux", "sd3", "sdxl")
    for marker in image_markers:
        if marker in lowered:
            return "image"
    non_chat = (
        "embedding",
        "embed-",
        "-embed",
        "rerank",
        "whisper",
        "tts",
        "-audio",
        "moderation",
    )
    for marker in non_chat:
        if marker in lowered:
            return "other"
    return "chat"


def provider_model_listing(
    repo_root: Path,
    profile: Mapping[str, Any],
    *,
    agent: str,
    adapter: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """List a provider's selectable models, asking the provider itself.

    Falls back to the profile's declared list only when the endpoint cannot be
    interrogated, and reports which source answered so the UI can say so.
    """
    declared = fallback_reasoning_levels(profile)
    agent_levels = agent_reasoning_levels(agent, adapter)
    fallback = allowed_reasoning_levels(declared, agent_levels)
    env_path = provider_env_file(repo_root, profile)

    def entry(model_id: str) -> dict[str, Any]:
        return {
            "id": model_id,
            "name": str(metadata.get(model_id, {}).get("name") or model_id),
            "reasoning_levels": fallback,
            # What this model can be used for; the UI keeps non-chat models out
            # of the Agent picker.
            "capability": model_capability(model_id),
        }

    metadata = provider_models(profile)
    if not env_path or not env_path.is_file():
        return {
            "source": "unconfigured",
            "error": "provider env file is missing",
            "models": [entry(model_id) for model_id in sorted(provider_models(profile))],
        }
    env = read_provider_env(env_path)
    ids, error = discover_models(profile, env, timeout=timeout)
    if error or not ids:
        return {
            "source": "declared",
            "error": error,
            "models": [entry(model_id) for model_id in sorted(metadata)],
        }
    return {"source": "provider", "error": None, "models": [entry(model_id) for model_id in ids]}


def validate_model_available(
    profile: Mapping[str, Any],
    env: Mapping[str, str],
    model: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Reject a model the provider does not list; ``None`` means acceptable.

    Silence is not rejection: an endpoint that cannot be interrogated leaves the
    model unchecked rather than inventing a verdict. But an endpoint that
    *did* answer and omitted this model is a definite no, and catching that is
    the difference between a preview error and a run that dies at request time.
    """
    ids, error = discover_models(profile, env, timeout=timeout)
    if error or not ids:
        return None
    if model in ids:
        return None
    return (
        f"provider {profile.get('id')!r} does not serve model {model!r}; "
        "it lists: " + ", ".join(ids)
    )


def model_reasoning_levels(
    repo_root: Path,
    profile: Mapping[str, Any],
    model: str,
    *,
    agent: str,
    adapter: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return the reasoning levels legal for one provider/model/Agent triple."""
    agent_levels = agent_reasoning_levels(agent, adapter)
    env_path = provider_env_file(repo_root, profile)
    declared = fallback_reasoning_levels(profile)
    if env_path and env_path.is_file():
        levels = discover_reasoning_levels(
            profile, read_provider_env(env_path), model, timeout=timeout
        )
        if levels:
            return {
                "source": "provider",
                "levels": allowed_reasoning_levels(levels, agent_levels),
            }
    return {
        "source": "declared",
        "levels": allowed_reasoning_levels(declared, agent_levels),
    }


def find_provider_for_env_file(
    repo_root: Path,
    config: Mapping[str, Any],
    env_file: Path | None,
) -> dict[str, Any] | None:
    """Find which provider profile owns an env file, if any."""
    if env_file is None:
        return None
    target = env_file.resolve()
    for profile in load_provider_profiles(repo_root, config).values():
        candidate = provider_env_file(repo_root, profile)
        if candidate and candidate == target:
            return profile
    return None
