---
trigger: always_on
description: Consult the codebase-memory-mcp knowledge graph for codebase and architecture questions.
---

## codebase-memory-mcp

This project uses `codebase-memory-mcp` to maintain a knowledge graph of the codebase.

**ALWAYS prefer MCP graph tools over grep/glob/file-search for code discovery.**

## Priority Order
1. `search_graph` — find functions, classes, routes, variables by pattern
2. `trace_path` — trace who calls a function or what it calls
3. `get_code_snippet` — read specific function/class source code
4. `query_graph` — run Cypher queries for complex patterns
5. `get_architecture` — high-level project summary

## Fallback to grep/glob
- Searching for string literals, error messages, configuration values
- Searching non-code files (Dockerfiles, shell scripts, configs)
- When MCP tools return insufficient results

## Examples
- Find a handler: `search_graph(name_pattern=".*Handler.*")`
- Trace callers: `trace_path(function_name="FunctionName", direction="inbound")`
- Read source snippet: `get_code_snippet(qualified_name="package.FunctionName")`
