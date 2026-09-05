# Highland: MCP and Tool Use

## Connecting the Model to Services

In the discovery tutorial, I showed Highland calling Atlas CRM, Pulse, Relay Desk, and Beacon while preparing a briefing for a meeting with Northwind Bank. In the retrieval tutorial, I explained how Highland finds indexed information before that agent loop begins. In this tutorial, I want to focus on the other side of the discovery flow: how Highland makes live services available to the model through the Model Context Protocol, or MCP.

A model cannot call a Python function or send an HTTP request by itself. It can only produce output. Tool use works because the application describes the available operations to the model, recognizes when the model requests one, executes that request in normal application code, and returns the result in the next model call.

MCP standardizes the boundary between the application and those operations. An MCP server can declare its tools, including their names, descriptions, and input schemas. An MCP client can discover those declarations and call a selected tool without needing custom integration code for every function.

Highland uses this boundary to connect six fictional systems:

| Connector | Service | Example tool |
| --- | --- | --- |
| `crm` | Atlas CRM | `get_customer` |
| `knowledge` | Archive | `get_document` |
| `support` | Relay Desk | `list_customer_tickets` |
| `observability` | Beacon | `query_deployment_metrics` |
| `communications` | Pulse | `list_messages` |
| `projects` | Track | `get_issue` |

Each system still has an ordinary HTTP API. The MCP server is an adapter in front of that API, not a replacement for the service itself. This is deliberate: it keeps the service boundary realistic while giving Highland one consistent protocol for discovering and calling tools.

I will follow the same Northwind briefing from the discovery tutorial. We will start inside Atlas CRM, see how its tools are declared, follow Highland as it discovers them, and then trace how a tool definition becomes a model request, an MCP call, and finally new context for the next model iteration.

## Technical Deep Dive

### 1. Define the MCP Server

Every Highland connector is created by the [`build_mcp` function](../src/highland_mocks/mcp_server.py#L18). It creates a `FastMCP` server, gives the server a name and instructions, and asks the selected system to register its tools:

```python
def build_mcp(connector: str) -> FastMCP:
    product = SERVICE_PRODUCTS[connector]
    mcp = FastMCP(
        name=f"Highland — {product}",
        instructions=(
            f"Atomic tools for the synthetic {product} service. "
            "Preserve record IDs and source URLs in downstream evidence."
        ),
    )
    SYSTEMS[connector].register_tools(
        mcp,
        SourceClient(connector, _base_url(connector)),
    )
    return mcp
```

The connector argument determines which system is exposed. Running `highland-mcp crm` creates the Atlas CRM MCP server, while `highland-mcp support` creates the Relay Desk server. The same small entry point can therefore run six separate MCP servers without combining their tools or data.

The final line of the command starts the selected server over standard input and output:

```python
build_mcp(args.connector).run(transport="stdio")
```

With this transport, Highland starts each connector as a child process and exchanges MCP messages through the process's input and output streams. There is no separate network port for the MCP protocol in this setup. The connector itself uses HTTP when it needs to reach the underlying mock service.

### 2. Declare the Service Tools

Each system owns a `register_tools` function. Atlas CRM declares its tools in [`systems/crm.py`](../src/highland_mocks/systems/crm.py#L139). Here is the complete declaration of the tool used in the first model step of our briefing:

```python
@mcp.tool()
def get_customer(customer_id: str) -> dict[str, Any]:
    """Get one CRM customer by stable ID, such as cus_northwind."""
    return client.get(f"/customers/{customer_id}")
```

This small function contains the information needed on both sides of the tool boundary:

1. The function name becomes the MCP tool name, `get_customer`.
2. The docstring becomes the description that tells the model when the tool is useful.
3. The typed parameter becomes a JSON input schema requiring a string named `customer_id`.
4. The function body defines what Highland actually does when the tool is called.

`FastMCP` derives the declaration from the function. The resulting MCP tool is equivalent to this shortened response:

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

The description and schema matter because the model initially sees the declaration, not the function body. A vague description makes it harder for the model to choose the right tool, while an incorrect schema makes it more likely to produce arguments the application cannot execute.

When the tool is eventually called, `SourceClient` sends a normal HTTP request to `/customers/{customer_id}` and returns the service's JSON response. MCP is therefore the model-facing contract, while HTTP remains the service-facing contract.

### 3. Tell Highland Which MCP Servers Exist

Declaring a tool on a server does not automatically make Highland aware of the server. Highland also needs the commands that start each connector. The default [`connector_commands` setting](../src/highland/settings.py#L12) declares them as a mapping:

```python
DEFAULT_CONNECTOR_COMMANDS = {
    name: ("highland-mcp", name)
    for name in (
        "crm",
        "knowledge",
        "support",
        "observability",
        "communications",
        "projects",
    )
}
```

This configuration separates knowing that a connector exists from knowing which tools it provides. Highland knows how to start `highland-mcp crm`, but it does not keep a hard-coded list of the CRM tools. The connector remains responsible for declaring its own capabilities.

The repository also includes an [`mcp.example.json`](../config/mcp.example.json) file showing the equivalent command, arguments, and environment variables for an external MCP client. Highland's runtime uses `connector_commands`, but both configurations express the same process boundary: start one command for each MCP server and give it the URL of the service it wraps.

### 4. Connect and Discover the Tools

At the beginning of discovery, [`DiscoverService`](../src/highland/discover/service.py#L256) constructs the gateway with the configured commands and starts it:

```python
gateway = MCPGateway(
    self.connector_commands,
    startup_timeout_seconds=self.connector_timeout_seconds,
    request_timeout_seconds=self.connector_timeout_seconds,
)
await gateway.start()
```

The [`MCPGateway`](../src/highland/runtime/mcp.py#L50) is Highland's MCP client and process supervisor. For each configured connector, it starts the process, creates an MCP client session, performs the protocol initialization, and asks the server to list its tools:

```python
parameters = StdioServerParameters(
    command=command[0],
    args=list(command[1:]),
    env=dict(os.environ),
)
read, write = await stack.enter_async_context(stdio_client(parameters))
session = await stack.enter_async_context(ClientSession(read, write, ...))

await session.initialize()
response = await session.list_tools()
```

`list_tools()` is the discovery step. Its response contains the name, description, and input schema of every tool registered by that MCP server. This means a connector can add or change a tool without requiring a matching list inside the gateway.

Highland then qualifies each discovered name with the connector name:

```python
for item in response.tools:
    qualified = self.qualify(connector, item.name)
    self._tools[qualified] = MCPTool(
        connector=connector,
        source_name=item.name,
        qualified_name=qualified,
        description=item.description or "",
        input_schema=dict(item.inputSchema),
    )
```

The server calls the tool `get_customer`, but Highland exposes it as `crm__get_customer`. Another server could also have a `get_customer` tool without creating a collision. The qualified name tells Highland which MCP session owns the requested operation, while `source_name` preserves the original name that the server understands.

If one connector fails to start, the gateway records that failure and continues connecting to the others. Healthy services can still contribute tools instead of the entire discovery run losing all tool access because one integration is unavailable.

### 5. Convert MCP Tools into Model Tools

The MCP response is not sent directly to Cohere. Highland first converts every discovered `MCPTool` into its provider-neutral [`ToolDefinition`](../src/highland/runtime/mcp.py#L18):

```python
def model_definition(self) -> ToolDefinition:
    return ToolDefinition(
        name=self.qualified_name,
        description=f"[{self.connector}] {self.description}".strip(),
        input_schema=self.input_schema,
    )
```

For Atlas CRM, that produces a definition like this:

```text
ToolDefinition(
    name="crm__get_customer",
    description="[crm] Get one CRM customer by stable ID, such as cus_northwind.",
    input_schema={
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"],
    },
)
```

Before the tools reach the model, the [`ToolRegistry`](../src/highland/runtime/policy.py#L58) provides the policy boundary. It can remove write tools from a read-only run, and it later validates requested arguments, customer scope, and approval requirements. Discovering a tool therefore does not mean that every run can execute it without checks.

On every iteration, the [`AgentLoop`](../src/highland/runtime/agent.py#L179) builds a `ChatRequest` containing the current messages, the allowed tool definitions, and the retrieved documents:

```python
request = ChatRequest(
    messages=self._bounded_messages(state.messages),
    tools=self.tools.model_tools(scope),
    documents=documents or [],
    required_capabilities=ModelCapabilities(tools=True),
)

response = await self.model.chat(request)
```

The [captured first request](data/1-discovery/chat-request.txt) from the Northwind run shows the real list. It includes tools from all six connected systems, such as `crm__get_customer`, `communications__list_messages`, and `observability__query_deployment_metrics`.

Finally, the [`_chat_arguments` adapter](../src/highland/models/cohere_mapping.py#L197) maps each provider-neutral definition into Cohere's function-tool format:

```python
arguments["tools"] = [
    {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }
    for tool in request.tools
]
```

The model now knows which operations are available and what arguments each one accepts. It still cannot execute those operations. It can only ask Highland to do so.

### 6. Let the Model Request a Tool

In the first iteration of the Northwind briefing, the model receives the user's question, the retrieved documents, and all the available tool definitions. It decides that it needs the current CRM record and returns a tool call instead of a final answer. The [captured response](data/1-discovery/chat-response.txt) contains:

```text
ChatResponse(
    message=Message(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(
                id="crm__get_customer_fhvzyts1zf7p",
                name="crm__get_customer",
                arguments={"customer_id": "cus_northwind"},
            )
        ],
    ),
    finish_reason="tool_call",
)
```

The tool call ID identifies this particular request. The name selects a discovered tool, and the arguments follow the JSON schema the model received. Highland's Cohere adapter converts the provider response into this common `ToolCall` contract so the rest of the runtime does not have to work with provider-specific objects.

This is an important distinction: the model requested `crm__get_customer`, but it did not call Atlas CRM. Highland remains in control of execution.

### 7. Validate and Execute the Request

The agent loop sends the model's requested name and arguments through the tool registry:

```python
checked = self.tools.validate(
    call.name,
    dict(call.arguments),
    run_id=run_id,
    logical_step_id=call.id,
    scope=scope,
)
```

Validation confirms that the tool exists, checks its input against the discovered JSON schema, applies the run's customer and write scope, and determines whether human approval is required. Read operations such as `crm__get_customer` can proceed immediately. Write operations can pause the run and wait for approval.

The registry then calls the gateway, which uses the qualified name to find the correct connection and sends the original tool name to that MCP server:

```python
tool = self._tools[qualified_name]
connection = self._connections[tool.connector]
result = await connection.session.call_tool(
    tool.source_name,
    dict(arguments),
)
```

For our example, `crm__get_customer` selects the CRM connection, while the MCP request itself calls `get_customer`. The connector executes the Python function, the function requests `/customers/cus_northwind` from Atlas CRM, and the JSON record travels back through the same boundaries.

The gateway normalizes the MCP result into text and structured content. Transport failures and service errors are also converted into tool results, allowing the model to observe that a call failed and decide what to do next instead of crashing the whole agent loop.

### 8. Return the Result in the Next Iteration

Highland appends both sides of the interaction to the conversation. First comes the assistant message containing the model's request. Then comes a tool message whose `tool_call_id` connects the result to that request:

```text
Message(
    role="assistant",
    tool_calls=[ToolCall(id="crm__get_customer_fhvzyts1zf7p", ...)],
)
Message(
    role="tool",
    tool_results=[
        ToolResult(
            tool_call_id="crm__get_customer_fhvzyts1zf7p",
            content='{"name": "Northwind Bank", "health": "watch", ...}',
        )
    ],
)
```

You can inspect the longer [captured message list](data/1-discovery/messages-with-tool-results.txt) from the real run. On the next loop iteration, this updated message history is placed in a new `ChatRequest`. The available tool definitions and retrieved documents are included again as well.

The model can now reason over the CRM result. In this run, it learned that Northwind's health was on watch and then requested `communications__list_messages`. That result exposed more customer context, so the next iteration requested support tickets. The sequence continued through incident and metric checks.

The [captured iteration summary](data/3-mcp-tools/agent-iterations.txt) shows the complete sequence:

```text
step 1 -> crm__get_customer(customer_id="cus_northwind")
step 2 -> communications__list_messages(customer_id="cus_northwind", limit=20)
step 3 -> support__list_customer_tickets(customer_id="cus_northwind")
step 4 -> observability__list_incidents(customer_id="cus_northwind", status="open")
step 5 -> observability__list_incidents(status="open")
step 6 -> observability__query_deployment_metrics(metric="retrieval.p95_ms", ...)
step 7 -> observability__query_deployment_metrics(metric="retrieval.error_rate", ...)
step 8 -> final answer
```

These were eight separate calls to the model. Each tool result changed the messages in the next request, which gave the model new information on which to base its next decision. The sequence was not encoded as a fixed chain of seven functions. It emerged one iteration at a time from the model's requests and Highland's execution of them.

On step eight, the model returned no tool calls. That is how the agent loop knew the investigation was complete and the response should be treated as the final answer.

## The Complete MCP and Tool-Use Path

The complete path begins with the service code. Each system registers typed Python functions on a `FastMCP` server. Highland starts the configured servers, initializes an MCP session with each one, and discovers their tools with `list_tools()`. It qualifies the names, applies runtime policy, converts the declarations into model tool definitions, and includes those definitions in every model request.

When the model needs live information, it returns a tool call containing a name and JSON arguments. Highland validates the request, routes it to the MCP server that owns the tool, and executes the underlying service operation. The result is appended to the conversation and sent back to the model in the next iteration. This continues until the model asks for no more tools and produces the final grounded answer.

MCP makes the service boundary consistent, but Highland still owns the agentic behavior around it: process supervision, name qualification, model adaptation, policy enforcement, approvals, error handling, iteration limits, and conversation state. The model chooses what it wants to do; the application decides what is available, what is allowed, how it runs, and what the model gets to observe afterward.
