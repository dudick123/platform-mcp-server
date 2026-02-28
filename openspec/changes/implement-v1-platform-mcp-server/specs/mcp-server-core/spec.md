## ADDED Requirements

### Requirement: MCP Server Entry Point

The server SHALL expose all registered tools via the Model Context Protocol stdio transport using FastMCP. The server process SHALL read from stdin and write to stdout per the MCP specification. No network listener SHALL be opened.

#### Scenario: Server starts and registers tools

- **WHEN** the server process is started via `uv run python -m platform_mcp_server.server`
- **THEN** all six tools are registered and available for invocation via MCP stdio transport

#### Scenario: Server runs without network listener

- **WHEN** the server is running
- **THEN** no TCP or UDP ports are opened; the only I/O channels are stdin, stdout, and stderr

### Requirement: Multi-Cluster Configuration

The server SHALL maintain a configuration mapping from composite cluster identifiers (`dev-eastus`, `dev-westus2`, `staging-eastus`, `staging-westus2`, `prod-eastus`, `prod-westus2`) to their corresponding Azure subscription ID, resource group, AKS cluster name, and kubeconfig context name. This mapping SHALL be defined in `config.py` and SHALL be the single source of truth for cluster resolution.

#### Scenario: Cluster identifier resolves to correct context

- **WHEN** a tool is invoked with `cluster="prod-eastus"`
- **THEN** the server resolves the kubeconfig context, Azure subscription, and resource group from the config mapping before making any API call

#### Scenario: Invalid cluster identifier rejected

- **WHEN** a tool is invoked with a cluster identifier not in the config mapping
- **THEN** the server returns a structured `ToolError` with a clear message listing valid cluster identifiers

### Requirement: Structured Error Model

All tools SHALL return errors using a consistent Pydantic `ToolError` model containing: `error` (human-readable summary), `source` (data source that failed), `cluster` (cluster context), and `partial_data` (boolean indicating whether partial results are included). Raw stack traces SHALL never reach tool output.

#### Scenario: Data source unavailable returns structured error

- **WHEN** a Kubernetes or Azure API call fails during tool execution
- **THEN** the tool catches the exception and returns a `ToolError` with the failing source identified and `partial_data=True` if other data was successfully retrieved

#### Scenario: Error output is LLM-safe

- **WHEN** a `ToolError` is returned
- **THEN** the error message contains no internal IP addresses, subscription IDs, resource group names, or credential values

### Requirement: Structured JSON Logging

The server SHALL use `structlog` to emit structured JSON logs to stderr. Each tool invocation SHALL log: tool name, parameters, data source latency, and success/failure status. Logs SHALL contain no PII, internal IPs, or credential values.

#### Scenario: Tool invocation is logged

- **WHEN** any tool is invoked
- **THEN** a JSON log entry is written to stderr containing the tool name, input parameters, data source latency in milliseconds, and whether the call succeeded or failed

### Requirement: Credential Isolation

The server SHALL source Azure credentials exclusively from `DefaultAzureCredential` (inheriting the engineer's `az login` session) and Kubernetes credentials from the `KUBECONFIG` environment variable. No credentials SHALL be hardcoded, logged, or included in tool output.

#### Scenario: Azure authentication uses DefaultAzureCredential

- **WHEN** the server makes an Azure AKS API call
- **THEN** authentication uses `DefaultAzureCredential` with no service principal credentials stored locally

#### Scenario: Kubernetes authentication uses kubeconfig

- **WHEN** the server makes a Kubernetes API call
- **THEN** the kubeconfig context is explicitly set from the config mapping; the active kubectl context is never relied upon

### Requirement: Configurable Thresholds

Operational thresholds (CPU pressure %, memory pressure %, pending pod limits, upgrade anomaly duration) SHALL be defined in `config.py` with sensible defaults and SHALL be overridable via environment variables. No threshold values SHALL be hardcoded in tool modules.

#### Scenario: Threshold defaults are applied

- **WHEN** no environment variable overrides are set
- **THEN** the server uses default thresholds: CPU critical ≥90%, memory critical ≥95%, pending pods critical >10, upgrade anomaly >60 minutes

#### Scenario: Environment variable overrides threshold

- **WHEN** an environment variable override is set (e.g., `PRESSURE_CPU_CRITICAL=85`)
- **THEN** the server uses the overridden value instead of the default

### Requirement: Output Scrubbing

All tool responses SHALL be scrubbed of internal IP addresses, Azure subscription IDs, and resource group names before being returned. Node names (e.g., `aks-userpool-000011`) are operational identifiers and SHALL be included in output.

#### Scenario: Internal IPs are scrubbed

- **WHEN** a tool response contains an internal IP address from the Kubernetes API
- **THEN** the IP is removed or redacted before the response is returned to the MCP client

#### Scenario: Node names are preserved

- **WHEN** a tool response contains AKS node names
- **THEN** the node names are included in the response without modification
