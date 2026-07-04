# Copyright 2026 The thingctx Authors
# SPDX-License-Identifier: Apache-2.0
"""The MCP bridge: a TD becomes MCP tools, callable over a real session."""

from __future__ import annotations

import pytest

from thingctx import LocalBinding, ThingClient

TD = {
    "@context": "https://www.w3.org/2022/wot/td/v1.1",
    "id": "urn:demo:pump:v1",
    "title": "Pump",
    "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
    "security": ["nosec_sc"],
    "actions": {
        "status": {"idempotent": True, "forms": [{"href": "local://status"}]},
        "set_speed": {
            "input": {"type": "object", "properties": {"rpm": {"type": "integer"}}},
            "forms": [{"href": "local://set_speed"}],
        },
    },
}


@pytest.mark.asyncio
async def test_td_becomes_callable_mcp_tools():
    pytest.importorskip("mcp")
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from thingctx.integrations.mcp import build_mcp_server

    inv = LocalBinding(
        {"status": lambda: {"rpm": 0}, "set_speed": lambda rpm=0: {"ok": True, "rpm": rpm}}
    )
    server = build_mcp_server(ThingClient(tds=[TD], bindings=[inv]), name="pump")
    async with connect(server) as s:
        await s.initialize()
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert "pump.set_speed" in tools
        # the risk hints come from the TD's own semantics
        assert tools["pump.status"].annotations.readOnlyHint is True
        # call a tool for real
        res = await s.call_tool("pump.set_speed", {"rpm": 1200})
        assert "1200" in res.content[0].text


CAMERA_TD = {
    "@context": "https://www.w3.org/2022/wot/td/v1.1",
    "id": "urn:demo:cam:v1",
    "title": "Camera",
    "actions": {"watch": {"forms": [{"href": "rtsp://cam/stream", "x-thingctx-media": {"k": 1}}]}},
}


@pytest.mark.asyncio
async def test_media_td_becomes_snapshot_image_tool():
    pytest.importorskip("mcp")
    pytest.importorskip("PIL")
    import threading

    import numpy as np
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from thingctx.bindings.builtin.media import Frame, MediaBinding
    from thingctx.integrations.mcp import build_mcp_server

    class _FakeBackend:
        def can_open(self, url, hint):
            return True

        def read(self, url, *, options, stop: threading.Event):
            yield Frame(data=np.zeros((4, 4, 3), dtype=np.uint8), kind="video", pts=0.0)

        def write(self, *a, **k):
            raise NotImplementedError

    client = ThingClient(tds=[CAMERA_TD], bindings=[MediaBinding(backends=[_FakeBackend()])])
    server = build_mcp_server(client, name="cam")
    media_name = client.list_media()[0]  # e.g. "cam.watch"
    snapshot = f"{media_name.split('.', 1)[0]}.snapshot"  # becomes "cam.snapshot"

    async with connect(server) as s:
        await s.initialize()
        tools = {t.name: t for t in (await s.list_tools()).tools}
        # the stream surfaces as a read only snapshot tool, not the stream name
        # and not an invoke action
        assert snapshot in tools
        assert media_name not in tools
        assert tools[snapshot].annotations.readOnlyHint is True
        # calling it returns one frame as MCP image content
        res = await s.call_tool(snapshot, {})
        assert res.content[0].type == "image"
        assert res.content[0].mimeType == "image/jpeg"
        assert res.content[0].data  # base64 jpeg


@pytest.mark.asyncio
async def test_media_snapshot_can_return_a_clip():
    """frames > 1 turns the snapshot tool into a short clip: several image
    blocks sampled over time (MCP has no video content type)."""
    pytest.importorskip("mcp")
    pytest.importorskip("PIL")
    import threading

    import numpy as np
    from mcp.shared.memory import create_connected_server_and_client_session as connect

    from thingctx.bindings.builtin.media import Frame, MediaBinding
    from thingctx.integrations.mcp import build_mcp_server

    class _ClipBackend:
        def can_open(self, url, hint):
            return True

        def read(self, url, *, options, stop: threading.Event):
            for i in range(20):
                yield Frame(data=np.zeros((4, 4, 3), dtype=np.uint8), kind="video", pts=float(i))

        def write(self, *a, **k):
            raise NotImplementedError

    client = ThingClient(tds=[CAMERA_TD], bindings=[MediaBinding(backends=[_ClipBackend()])])
    server = build_mcp_server(client, name="cam")
    snapshot = f"{client.list_media()[0].split('.', 1)[0]}.snapshot"

    async with connect(server) as s:
        await s.initialize()
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert "frames" in tools[snapshot].inputSchema["properties"]
        res = await s.call_tool(snapshot, {"frames": 3, "every": 2.0})
        images = [c for c in res.content if c.type == "image"]
        assert len(images) == 3
        assert all(c.mimeType == "image/jpeg" and c.data for c in images)
