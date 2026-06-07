# thingctx

**One standard description, and your model can read context from and take
actions on a real device, sensor, tool, or service, directly. No
per-integration server.**

thingctx uses the [W3C Web of Things](https://www.w3.org/WoT/) standard as a
uniform interface between an AI application and the systems it needs to
reach. Point it at a Thing Description and it drives the actual Thing the
description names, over that Thing's own transport. The description is how
you integrate; the device or service is what you act on. The integration is
a document, not a server you run.

A "Thing" here is anything with a callable interface, not just hardware: a
sensor or a robot, but equally a REST API, a database, a SaaS product, an
internal service. A Thing Description (TD) is plain JSON that names that
system's `actions` (things to do), `properties` (state to read or write),
and `events` (things to subscribe to), plus the transport for each (HTTP,
MQTT, local, and more). thingctx reads it, hands the actions to your model
as tools, and calls each against the real system over the transport the TD
names. The system's own endpoints are the server; you write nothing on the
server side.

A whole TD can be this small (a weather API, no hardware in sight):

```json
{
  "@context": "https://www.w3.org/2022/wot/td/v1.1",
  "id": "urn:example:weather:v1",
  "title": "Weather",
  "securityDefinitions": { "bearer_sc": { "scheme": "bearer" } },
  "security": ["bearer_sc"],
  "properties": {
    "temperature": { "type": "number", "readOnly": true,
      "forms": [{ "href": "https://api.example.com/temp" }] }
  },
  "actions": {
    "forecast": {
      "input": { "type": "object", "properties": { "city": { "type": "string" } } },
      "forms": [{ "href": "https://api.example.com/forecast", "htv:methodName": "POST" }]
    }
  }
}
```

That document is the integration. Point an agent at it:

```python
import thingctx

host = await thingctx.from_url("https://api.example.com/.well-known/wot")
print(await host.chat("what's the forecast for Cairo, and the current temperature?"))
```

The model picks the actions; thingctx routes each to its transport.

## Install

```bash
pip install thingctx[all]      # litellm + httpx + paho-mqtt + jsonschema + mcp
# or pick extras: thingctx[llm] thingctx[http] thingctx[mqtt] thingctx[validate] thingctx[mcp]
```

## Drive it directly, no server, no MCP

When you own the agent loop, integrate the Thing straight into it: read a
description, get the tool specs to hand your model, route each call back to
the Thing. Nothing in between.

```python
import thingctx

client = thingctx.ThingClient.from_registry(
    thingctx.from_arg("http://device.local/.well-known/wot"))   # a URL, folder, or TDD
specs, invoke = client.as_tools()        # specs for your model; invoke(name, args) runs a call

await invoke("pump.set_speed", {"rpm": 1500})
await client.read_property("pump.rpm")
```

The description and the Thing's own endpoints are the whole integration.
Add a Thing by pointing at one more description.

## Reach a closed agent: the MCP bridge

The direct path works when you write the loop yourself, because you can pass
the tool specs straight to your model. But some agents are closed: you
cannot give their model tools directly, only through MCP (Claude Desktop, the
Claude CLI, Copilot). For those, thingctx ships a bridge: one generic
MCP server that turns a registry of descriptions (a folder, a URL, or a
W3C Thing Description Directory) into MCP tools, with no per-integration
server.

```bash
pip install "thingctx[mcp,http]"
thingctx-mcp ./examples/registry/        # a folder, a URL, or a TD Directory
```

```json
{ "mcpServers": { "things": { "command": "thingctx-mcp",
  "args": ["./examples/registry/"] } } }
```

thingctx is not an MCP server for the Web of Things. The integration is the
description; MCP is just one way to deliver it, to an agent where direct
tool calling is not available.

## Why not MCP

To expose a system over MCP you write a server, deploy it, and keep it
running, one per integration. N systems means N processes to operate. A Thing
Description is a static file: write it (or generate it), check it into git,
or serve it from a URL. There is no process to run, nothing to keep alive.
thingctx reads the document and calls the endpoints it names. Integration
becomes data, not a service, and data scales to a fleet for free.

A messy device (binary protocol, a session dance) gets one thin connector
that exposes a clean WoT face; the TD describes *that*. Either way you
write no server per agent integration. The connector is consumed the same way
by an LLM, an MCP client, or anything else.

For comparison, see [`examples/01_mcp_baseline.py`](examples/01_mcp_baseline.py)
(MCP, a server per integration) and
[`examples/02_thingctx_baseline.py`](examples/02_thingctx_baseline.py)
(thingctx, no server). Both drive the same pump; every result is asserted
equal to calling the system directly.

## ThingClient: the core

The core is stdlib only, with no dependency on any agent framework.
`ThingClient` has no LLM and no opinion on what chose the action. It reads
properties, writes them, and streams events, and routes each call to the
transport the TD's form names, so one client can read over HTTP and
subscribe over MQTT without you wiring either:

```python
await client.read_property("pump.rpm")          # e.g. an HTTP GET
await client.write_property("pump.target_rpm", 1500)
async for evt in await client.subscribe("pump.overheat"):   # e.g. an MQTT topic
    ...                              # evt is the payload, e.g. {"temp": 98}
```

A text LLM, a vision model, or your own code can drive it; `invoke` is the
same. (`thingctx.from_url(...)` returns a ready `LLMHost` if you just want a
loop out of the box.)

## Where the TDs live: a registry

A registry is wherever your descriptions come from: a folder of files, a
URL, or a **Thing Description Directory** (TDD). It is a general source, not
tied to any one consumer. `ThingClient`, the MCP bridge, and the LLM loop all
build from the same registry.

```python
client = thingctx.ThingClient.from_registry(thingctx.from_arg("./examples/registry/"))
```

The TDD is not a thingctx invention. It is the
[W3C WoT Discovery](https://www.w3.org/TR/wot-discovery/) standard (a final
Recommendation): a service that serves a whole fleet of Things from a
`/things` endpoint, with optional search. thingctx reads from any compliant
TDD. Point `from_arg` at its URL.

## Authentication

The TD declares the scheme (`bearer`, `basic`, `apikey`); the secret is
supplied to the invoker at runtime, never in the TD. So a TD is safe to
commit and share.

```python
thingctx.HttpInvoker(credentials={"my_token": "secret"})  # key = the scheme name
```

## License

Apache-2.0. Copyright 2026 The thingctx Authors.
