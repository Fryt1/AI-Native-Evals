"""Provider-neutral MCP descriptors and standard ACP projection."""

from __future__ import annotations

from typing import Any


def project_dsh_mcp_servers(servers: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert shared descriptors to the standard ACP ``McpServer`` shape.

    DSH's ACP bridge consumes the Agent Client Protocol declaration, not the
    internal ``dsh-mcp-client`` configuration. Keeping this projection here
    prevents the Docker runtime and task definitions from knowing either wire
    format.
    """
    projected: list[dict[str, Any]] = []
    for name, descriptor in servers.items():
        if not isinstance(descriptor, dict):
            raise ValueError(f"MCP server {name!r} must be an object")
        transport = descriptor.get("transport") or (
            "streamable-http" if descriptor.get("url") else "stdio"
        )
        if transport == "stdio":
            command = descriptor.get("command")
            if not command:
                raise ValueError(f"MCP stdio server {name!r} needs command")
            environment = [
                {"name": str(key), "value": str(value)}
                for key, value in (descriptor.get("env") or {}).items()
            ]
            projected.append(
                {
                    "name": name,
                    "command": str(command),
                    "args": [str(value) for value in descriptor.get("args", [])],
                    "env": environment,
                }
            )
        elif transport in {"http", "streamable-http"}:
            url = descriptor.get("url")
            if not url:
                raise ValueError(f"MCP HTTP server {name!r} needs url")
            headers = [
                {"name": str(key), "value": str(value)}
                for key, value in (descriptor.get("headers") or {}).items()
            ]
            projected.append(
                {
                    "type": "http",
                    "name": name,
                    "url": str(url),
                    "headers": headers,
                }
            )
        else:
            raise ValueError(f"unsupported MCP transport for {name!r}: {transport}")
    return projected
