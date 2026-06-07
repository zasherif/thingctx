# Mapping a Thing Description to agent tools

This is the one thing thingctx defines that is not already a W3C standard: how a
[Thing Description](https://www.w3.org/TR/wot-thing-description11/) (TD) becomes
the tools an LLM agent calls, and how a tool call is routed back to the real
system. The TD format, the transport bindings, and discovery are all W3C
Recommendations; this mapping is the layer on top.

It is written so anyone can implement the same mapping and get the same tools
from the same TD. thingctx is one implementation.

## The shape

```
TD affordance  ──►  tool spec (what the model sees)
tool call      ──►  form invocation (the real HTTP/MQTT/... request)
```

An action becomes a callable tool. A property becomes read/write calls. An event
becomes a subscription. The model only ever sees tool specs; thingctx routes each
call back to the form the TD names.

## Actions → tools

Each `action` in the TD becomes one tool.

**Name.** `<thing-slug>.<actionName>`. The slug is the last meaningful segment of
the Thing's `id`, with a trailing version token (`v1`, `2`, ...) dropped:

| Thing `id` | action | tool name |
|--|--|--|
| `urn:demo:pump:v1` | `setSpeed` | `pump.setSpeed` |
| `urn:svc:github` | `createIssue` | `github.createIssue` |

Namespacing by Thing keeps actions from colliding across a fleet.

**Parameters.** The action's `input` JSON Schema becomes the tool's `parameters`
verbatim. If there is no `input`, the tool takes `{"type": "object"}` (no args).

**Description.** The action's `description`. WoT actions also allow an `output`
schema; since the common tool format (OpenAI functions) has no output field, the
output schema is folded into the description as `Returns: <schema>`.

Example — this action:

```json
"createIssue": {
  "input": { "type": "object", "properties": {
    "owner": {"type": "string"}, "repo": {"type": "string"},
    "title": {"type": "string"}, "body": {"type": "string"} } },
  "forms": [{ "href": "https://api.github.com/repos/{owner}/{repo}/issues",
              "htv:methodName": "POST" }]
}
```

on a Thing `urn:svc:github` becomes this tool spec:

```json
{ "type": "function", "function": {
  "name": "github.createIssue",
  "description": "createIssue",
  "parameters": { "type": "object", "properties": {
    "owner": {"type": "string"}, "repo": {"type": "string"},
    "title": {"type": "string"}, "body": {"type": "string"} } } } }
```

## Tool call → form invocation

When the model calls a tool, it is routed to the matching action's `form`, and the
arguments are placed according to the binding.

**1. Path templating first.** Any `{var}` in the form's `href` is filled from the
arguments and consumed. So `createIssue(owner="my-org", repo="api", ...)` against
`href: .../repos/{owner}/{repo}/issues` produces the URL
`.../repos/my-org/api/issues`, and `owner`/`repo` are removed from the remaining
arguments.

**2. The rest of the arguments go by transport binding.** For HTTP, following the
[WoT HTTP binding](https://www.w3.org/TR/wot-binding-templates/):

- The method is the form's `htv:methodName` if declared.
- If not declared, it defaults by safety: an `idempotent` action uses `GET`
  (remaining args become query parameters); any other action uses `POST`
  (remaining args become a JSON body).

So in the GitHub example: `POST .../repos/my-org/api/issues` with body
`{"title": ..., "body": ...}` — exactly the GitHub REST API.

**Properties.** A readable property reads with `GET` on its form; a writable
property writes the value (HTTP `PUT` by default). **Events** and observable
properties subscribe over the form's streaming binding (Server-Sent Events for
HTTP, the topic for MQTT).

## Security

The TD declares *which* scheme an interaction needs in `securityDefinitions`; the
**secret is never in the TD**. It is supplied to the client at run time, keyed by
the scheme name, so a TD is safe to commit and share. Each scheme maps to a
request modification:

| scheme | applied as |
|--|--|
| `bearer` | `Authorization: Bearer <secret>` |
| `basic`  | `Authorization: Basic <base64(secret)>` |
| `apikey` (`in: header`) | header `<name>: <secret>` |
| `apikey` (`in: query`)  | query param `<name>=<secret>` |
| `nosec`  | nothing |

## Transports

The form's `href` scheme selects the binding: `http(s)://` → HTTP, `mqtt://` →
MQTT, no scheme → a local handler. One Thing can mix them: read a property over
HTTP, subscribe to an event over MQTT, in the same TD. The mapping above is per
form, so each interaction uses the transport its own form names.

## Honest gaps (current)

This documents the mapping as implemented today, not an aspiration:

- The tool spec uses the **OpenAI function** shape. Other agent runtimes use
  different shapes; an implementation may translate, but this doc specifies the
  OpenAI form.
- **Events as tools:** events are exposed as subscriptions, not as callable
  tools. An agent that only does request/reply tool calls does not see events.
- **`uriVariables` typing:** path/query variables are substituted by name from
  the flat argument object; their declared schemas are not separately enforced
  at the mapping layer.
- Only the **HTTP, MQTT, and local** bindings are mapped. Other WoT bindings
  (CoAP, WebSocket, ...) are not yet implemented.

## Why document this

The mapping is the interoperability surface. Two implementations that follow it
produce the same tools from the same TD, so a TD authored once works the same
whether driven by thingctx, an MCP server built on it, or another SDK. It is a
small convention, not a wire protocol; the natural long-term home is a W3C WoT
note on consuming Thing Descriptions as agent tools, with this as the de-facto
starting point.
