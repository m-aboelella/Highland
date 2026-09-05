# Highland: MCP and Tool Use

## Giving the Model Access to Live Services

In the discovery tutorial, I showed Highland using retrieval and live tools to prepare a briefing for a meeting with Northwind Bank. In this tutorial, I want to focus on the live-tool side of that flow: how Highland learns which tools exist, lets the model request one, and returns the result to the model.

A tool is useful when the information is not in the search index, when the indexed copy may be stale, or when the user wants the agent to perform an action. The model cannot call a Python function or an HTTP API by itself. It can only ask the application to make the call.

MCP, or the Model Context Protocol, provides a standard connection between the application and the service that owns the tool. The easiest way to picture the boundary is:

```text
model  <->  Highland  <->  MCP server  <->  Atlas CRM HTTP API
```

Atlas CRM owns the MCP server and declares its CRM tools. Highland connects to that server, discovers the tools, and tells the model which ones it may use. When the model asks for a tool, Highland validates the request and sends it to the MCP server.

I will follow one small example throughout the tutorial. The user asks Highland for the current status of Northwind Bank. Highland exposes one Atlas CRM tool, `get_customer`, and the model requests that tool before answering.

The flow has seven stages:

1. Atlas CRM creates an MCP server and registers `get_customer`.
2. Atlas CRM chooses how clients connect to that server.
3. Highland connects and discovers the tool.
4. Highland gives the tool definition to the model.
5. The model asks Highland to use the tool.
6. Highland validates and executes the request.
7. Highland returns the result to the model.

The first two stages happen on the Atlas CRM side. The remaining stages happen inside Highland. Keeping those two sides separate makes the rest of the flow easier to follow.

Atlas CRM is only one of Highland's connected services. Highland also connects to knowledge, support, observability, communications, and project systems. This tutorial uses only Atlas CRM so that we can follow one MCP connection without repeating the same pattern for every service.

Throughout this tutorial, code names appear in monospace, such as `build_mcp`. When the real implementation is available, a separate link such as [source](../src/highland_mocks/mcp_server.py#L18) appears beside it. This keeps code names readable and makes links easy to spot.

## Part One: The Atlas CRM MCP Server

The MCP server belongs to the service integration. It can live in another repository and be used by any application that supports MCP. It does not need to know which model the application uses or how the application runs its agent loop.

### 1. Register the CRM Tool

The real demo keeps its reusable server setup in `build_mcp` ([source](../src/highland_mocks/mcp_server.py#L18)) and its CRM tools in `crm.register_tools` ([source](../src/highland_mocks/systems/crm.py#L139)). It uses `SourceClient` ([source](../src/highland_mocks/systems/common.py#L121)) to call the underlying mock HTTP API.

Here is the same idea written as a small standalone Atlas CRM server:

```python
import os

import httpx
from mcp.server.fastmcp import FastMCP


ATLAS_CRM_URL = os.getenv("ATLAS_CRM_URL", "http://localhost:8101")

mcp = FastMCP(
    name="Atlas CRM",
    instructions="Tools for reading customer records from Atlas CRM.",
)


@mcp.tool()
def get_customer(customer_id: str) -> dict:
    """Get one CRM customer by stable ID, such as cus_northwind."""
    response = httpx.get(
        f"{ATLAS_CRM_URL}/customers/{customer_id}",
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
```

The server code contains no Highland or model-provider imports. It only knows how to describe the CRM tool and how to translate that tool call into an Atlas CRM HTTP request.

The `@mcp.tool()` decorator registers `get_customer` with the server. `FastMCP` uses the function name, docstring, and typed parameter to produce a declaration equivalent to:

```text
name: get_customer
description: Get one CRM customer by stable ID, such as cus_northwind.
inputSchema:
  type: object
  properties:
    customer_id:
      type: string
  required: [customer_id]
```

An MCP client receives this declaration by calling `list_tools()`. The client does not need the server's Python source code and does not keep a second hard-coded copy of the schema.

If a client later calls `get_customer` with `customer_id="cus_northwind"`, FastMCP runs the decorated function. The function sends `GET /customers/cus_northwind` to Atlas CRM and returns the JSON response as the tool result.

The real CRM integration also registers `list_customers`. You can see both complete declarations in `crm.register_tools` ([source](../src/highland_mocks/systems/crm.py#L139)); the tutorial follows only `get_customer` so that one tool remains visible from beginning to end.

### 2. Choose the Transport

The tool does not change when the transport changes. The transport only determines how MCP messages move between the client and server. The [MCP transport specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) defines `stdio` and Streamable HTTP as the standard choices.

| | `stdio` | Streamable HTTP |
| --- | --- | --- |
| Who starts the server? | The client launches a child process | The server is started independently |
| How does the client find it? | A command and its arguments | An HTTP URL |
| How do messages travel? | Through the process's stdin and stdout | Through HTTP requests and responses |
| Typical use | Local tools and development | Shared or remote services |

For `stdio`, Atlas CRM starts FastMCP like this:

```python
mcp.run(transport="stdio")
```

The client launches the program and communicates through its process streams. There is no MCP port or URL:

```text
Highland  ->  starts Atlas CRM MCP process
Highland  ->  writes MCP messages to stdin
Highland  <-  reads MCP messages from stdout
```

For Streamable HTTP, Atlas CRM starts the same tools as a web service:

```python
mcp.run(transport="streamable-http")
```

The client connects to an endpoint such as `http://127.0.0.1:8000/mcp`:

```text
Highland  <->  http://127.0.0.1:8000/mcp
```

Streamable HTTP can return a normal JSON response or use Server-Sent Events when a response needs to stream. It should not be confused with FastMCP's older `transport="sse"` compatibility option.

Highland currently uses `stdio`. Its real MCP command ends with `run(transport="stdio")` ([source](../src/highland_mocks/mcp_server.py#L40)), and its gateway creates a `StdioServerParameters` ([source](../src/highland/runtime/mcp.py#L95)) for each connector. Streamable HTTP is shown because an independently hosted MCP server can choose that transport, but Highland's current gateway would need a URL-based connector configuration before it could use one.

## Part Two: Highland as the MCP Client

We can now cross the MCP boundary. From this point onward, every step happens inside Highland. Atlas CRM does not know what the user asked, which model Highland uses, or how Highland stores the conversation.

### 3. Connect and Discover the CRM Tool

Highland first needs to know how to start each MCP server. The real `DEFAULT_CONNECTOR_COMMANDS` ([configuration](../src/highland/settings.py#L12)) contains all six demo connectors. For this one-server example, the relevant entry is simply:

```python
atlas_crm_commands = {
    "crm": ("highland-mcp", "crm"),
}
```

The key `crm` is Highland's local name for the connection. The tuple is the command that starts the Atlas CRM MCP server.

At the beginning of a discovery run, the real `DiscoverService` ([source](../src/highland/discover/service.py#L256)) creates an `MCPGateway` with those commands and starts it. The gateway's `_connect` method ([source](../src/highland/runtime/mcp.py#L88)) launches each process, opens an MCP session, initializes it, and requests its tools.

Here is that connection sequence for Atlas CRM alone:

```python
parameters = StdioServerParameters(
    command="highland-mcp",
    args=["crm"],
    env=dict(os.environ),
)

async with stdio_client(parameters) as (read_stream, write_stream):
    async with ClientSession(read_stream, write_stream) as crm_session:
        await crm_session.initialize()
        discovered = await crm_session.list_tools()
```

`stdio_client(...)` starts the server process. `ClientSession` speaks MCP over the process streams. `initialize()` performs the protocol handshake, and `list_tools()` asks the server what it provides.

The response contains the `get_customer` declaration from part one. Highland learned about the tool through MCP; it did not import the Python function from the CRM server.

If Highland supported Streamable HTTP connectors, only the outer connection would change. The MCP Python SDK documents this client in [Writing MCP Clients](https://py.sdk.modelcontextprotocol.io/v1/client/):

```python
async with streamable_http_client(
    "http://127.0.0.1:8000/mcp"
) as (read_stream, write_stream, _):
    async with ClientSession(read_stream, write_stream) as crm_session:
        await crm_session.initialize()
        discovered = await crm_session.list_tools()
```

The same `ClientSession`, `initialize()`, and `list_tools()` calls work with either transport. The production gateway also keeps each session open, applies timeouts, and records one connector's failure without discarding healthy connections.

### 4. Give the Tool Definition to the Model

After discovery, Highland stores each server tool as an `MCPTool` ([source](../src/highland/runtime/mcp.py#L18)). The server calls the tool `get_customer`, but Highland qualifies it as `crm__get_customer` so tools from different servers cannot collide.

A simplified conversion looks like this:

```python
get_customer_tool = ToolDefinition(
    name="crm__get_customer",
    description=f"[crm] {discovered_tool.description}",
    input_schema=dict(discovered_tool.inputSchema),
)
```

The real conversion is in `MCPTool.model_definition` ([source](../src/highland/runtime/mcp.py#L26)). Highland keeps both names: `crm__get_customer` is the name shown to the model, while `get_customer` remains the name understood by the CRM server.

On every agent iteration, the real `AgentLoop` ([source](../src/highland/runtime/agent.py#L186)) builds a `ChatRequest` containing the conversation, the permitted tools, and any retrieved documents. For our focused example, the request is:

```python
conversation = [
    Message(
        role="user",
        content="Give me the current status of Northwind Bank.",
    )
]

request = ChatRequest(
    messages=conversation,
    tools=[get_customer_tool],
    documents=[],
    required_capabilities=ModelCapabilities(tools=True),
)

response = await model.chat(request)
```

The empty `documents` list keeps this tutorial focused on MCP. In the full discovery flow, Highland can send retrieved documents and tool definitions in the same request.

Highland uses provider-neutral `ToolDefinition` objects ([source](../src/highland/models/contracts.py#L37)) inside the agent loop. The `_chat_arguments` adapter ([source](../src/highland/models/cohere_mapping.py#L197)) converts them into Cohere's tool format immediately before the API call. This keeps the MCP and agent code independent of the model provider.

### 5. Receive the Model's Tool Request

The model sees the name, description, and schema for `crm__get_customer`. It decides that it needs Northwind Bank's current CRM record, so it returns a tool request instead of a final answer:

```python
tool_call = ToolCall(
    id="call_1",
    name="crm__get_customer",
    arguments={"customer_id": "cus_northwind"},
)
```

The tool-call ID identifies this particular request. The name selects the tool, and the arguments follow the schema that came from Atlas CRM.

Highland's `ToolCall` contract ([source](../src/highland/models/contracts.py#L43)) gives the agent loop one provider-neutral representation. The Cohere adapter's `_tool_call` function ([source](../src/highland/models/cohere_mapping.py#L113)) parses Cohere's response into that contract. You can also inspect the full Northwind response ([capture](data/1-discovery/chat-response.txt)).

The model has only requested the tool. Highland still controls whether the call is allowed and whether it is executed.

### 6. Validate and Execute the Request

Before the agent loop starts, `DiscoverService` builds the tool registry ([source](../src/highland/discover/service.py#L263)) from the connected gateway and `tool_policy.json` ([configuration](../config/tool_policy.json)). The real agent loop passes every model request to `ToolRegistry.validate` ([source](../src/highland/runtime/policy.py#L105)). Validation confirms that the tool exists, checks the arguments against the discovered JSON schema, and enforces the customer and write scope for the run. `RunScope` ([source](../src/highland/runtime/policy.py#L20)) records which customers and operations this run may access.

For Northwind Bank, the simplified call is:

```python
northwind_scope = RunScope(
    allowed_customers=frozenset({"cus_northwind"}),
    allow_writes=False,
)

checked_call = tool_registry.validate(
    tool_call.name,
    dict(tool_call.arguments),
    run_id="northwind-briefing",
    logical_step_id=tool_call.id,
    scope=northwind_scope,
)

normalized_result = await tool_registry.execute(checked_call)
```

`get_customer` is a read operation, so it can proceed immediately after validation. A write tool may instead pause the run and require human approval.

The call to `ToolRegistry.execute` ([source](../src/highland/runtime/policy.py#L165)) delegates to the gateway. The gateway's real `call` method ([source](../src/highland/runtime/mcp.py#L133)) finds the CRM session and sends the original server-side name, `get_customer`, with the validated arguments.

This name change is important. The model requested Highland's qualified name, `crm__get_customer`, but the MCP server receives the name it published, `get_customer`. The server then runs the decorated function and requests `/customers/cus_northwind` from Atlas CRM.

The gateway also converts transport failures and service errors into normal tool results. That lets the model observe a failed call and decide what to do next instead of crashing the entire agent loop.

### 7. Return the Result to the Model

The gateway normalizes the MCP response into a `NormalizedToolResult` ([source](../src/highland/runtime/mcp.py#L34)). The real agent loop converts that value into Highland's `ToolResult` message contract ([source](../src/highland/models/contracts.py#L49)), appends it to the conversation, and starts the next iteration in `AgentLoop.run` ([source](../src/highland/runtime/agent.py#L270)).

A simplified version is:

```python
conversation.append(
    Message(
        role="assistant",
        tool_calls=[tool_call],
    )
)
conversation.append(
    Message(
        role="tool",
        tool_results=[
            ToolResult(
                tool_call_id=tool_call.id,
                content=normalized_result.content,
            )
        ],
    )
)

next_request = ChatRequest(
    messages=conversation,
    tools=[get_customer_tool],
    documents=[],
    required_capabilities=ModelCapabilities(tools=True),
)
final_response = await model.chat(next_request)
```

The shared tool-call ID connects the CRM result to the model's request. The next model call sees the original question, its own tool request, and the returned CRM record. It can now answer that Northwind Bank's current health is `watch`.

You can inspect the complete message history in `messages-with-tool-results.txt` ([capture](data/1-discovery/messages-with-tool-results.txt)). In the real briefing, the model continued to request messages, tickets, incidents, and metrics before it answered.

For the one-tool teaching example, the loop is simply:

```text
step 1 -> model requests crm__get_customer(customer_id="cus_northwind")
          Highland calls Atlas CRM's get_customer tool
step 2 -> model answers using the returned CRM record
```

The sequence was not programmed as two fixed steps. After each model response, Highland checks whether the response contains another tool request. If it does, the loop validates and executes it. If it does not, `AgentLoop.run` ([source](../src/highland/runtime/agent.py#L201)) treats the response as the final answer.

## Summary of the MCP Flow

Here is the complete flow. Atlas CRM declares `get_customer` on its MCP server. Highland starts the server, opens an MCP session, and discovers the tool. Highland qualifies the tool's name, applies its policies, converts the declaration into the model provider's format, and includes it in the model request.

When the model asks for `crm__get_customer`, Highland validates the request and routes it to the Atlas CRM MCP server using the original name, `get_customer`. The MCP server calls the Atlas CRM HTTP API and returns the record. Highland adds that result to the conversation and calls the model again.

MCP standardizes the service boundary. Highland still owns the agent behavior around it: model requests, tool policy, approvals, error handling, iteration limits, and conversation state.
