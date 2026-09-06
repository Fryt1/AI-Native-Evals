"""Provider-neutral MCP descriptors and DSH ACP projection."""

from __future__ import annotations

from typing import Any


def project_dsh_mcp_servers(servers: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the shared MCP descriptor shape to DSH ACP's MCP shape."""
    projected: list[dict[str, Any]] = []
    for name, descriptor in servers.items():
        if not isinstance(descriptor, dict):
            raise ValueError(f"MCP server {name!r} must be an object")
        transport = descriptor.get("transport") or (
            "streamable-http" if descriptor.get("url") else "stdio"
        )
        timeout_ms = int(descriptor.get("tool_timeout_sec", 900)) * 1000
        common = {
            "serverName": name,
            "toolCallTimeoutMs": timeout_ms,
            "failOnStartupError": bool(descriptor.get("fail_on_startup_error", True)),
        }
        if transport == "stdio":
            if not descriptor.get("command"):
                raise ValueError(f"MCP stdio server {name!r} needs command")
            projected.append(
                {
                    **common,
                    "transport": "stdio",
                    "command": str(descriptor["command"]),
                    "args": [str(value) for value in descriptor.get("args", [])],
                    "env": {
                        str(key): str(value)
                        for key, value in (descriptor.get("env") or {}).items()
                    },
                    "cwd": str(descriptor.get("cwd", "/workspace/game-engine")),
                }
            )
        elif transport in {"http", "streamable-http"}:
            if not descriptor.get("url"):
                raise ValueError(f"MCP HTTP server {name!r} needs url")
            projected.append(
                {
                    **common,
                    "transport": "streamable-http",
                    "url": str(descriptor["url"]),
                    "headers": {
                        str(key): str(value)
                        for key, value in (descriptor.get("headers") or {}).items()
                    },
                }
            )
        else:
            raise ValueError(f"unsupported MCP transport for {name!r}: {transport}")
    return projected
