# Examples

Run from the repo root with `PYTHONPATH=src`.

| | |
|--|--|
| [01_mcp_baseline.py](01_mcp_baseline.py) | The MCP model: author + run a server. |
| [02_thingctx_baseline.py](02_thingctx_baseline.py) | Same pump, no server. Read 01/02 back to back. |
| [03_thingctx_llm.py](03_thingctx_llm.py) | Add an LLM (local Ollama or an API key). |
| [04_trust.py](04_trust.py) | Approval gating + `verify()` grounding (no model). See [docs/TRUST.md](../docs/TRUST.md). |
| [05_oauth2.py](05_oauth2.py) | A full OAuth2 client-credentials flow, offline: local token server + protected API, driven from a TD. |
| [06_custom_auth.py](06_custom_auth.py) | Extensible auth: register a new security scheme, and use the built-in AWS SigV4 signer. Offline. |
| [registry/](registry/) | Standalone TDs. Point `thingctx-mcp` or `from_registry` here. |

01/02 need no model. The pump device is [_pump.py](_pump.py) (HTTP + SSE + MQTT).

## Media (audio/video)

The continuous-binary plane: consume frames from a source, or publish them to a
target. Needs PyAV (`pip install 'thingctx[media]'`); a few are gated on
`mediamtx`/`ffmpeg` or an LLM key.

| | |
|--|--|
| [07_media_live.py](07_media_live.py) | Consume a stream resolved from a web page (yt-dlp); optional authenticated/private path. |
| [08_media_vlm.py](08_media_vlm.py) | Feed a sampled frame to a VLM. |
| [09_rtsp_local.py](09_rtsp_local.py) | Authenticated RTSP end to end, fully local (MediaMTX + auth layer). |
| [10_media_clip.py](10_media_clip.py) | Multi-frame sampling from a clip for a VLM. |
| [11_media_video.py](11_media_video.py) | Parameterized video-understanding service: pass a clip URL at call time. |
| [12_media_publish.py](12_media_publish.py) | The publish path; encode and push frames to a file or live RTSP (round trip verified). |
