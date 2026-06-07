"""Bridges that expose a thingctx Thing to other agent ecosystems.

mcp: a generic stdio MCP server. Point it at any TD and the device's
actions become MCP tools for any MCP client (Claude CLI, Copilot CLI, ...).
You write no MCP server per device; the TD already describes it.
"""
