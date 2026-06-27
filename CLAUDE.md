# AI Agent Cognitive Workflow and Memory Guide


## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)
You must actively traverse and respect the three cognitive memory layers before reasoning or executing commands:

1. **L0: Working Memory (Session & Developer Context)**
   - **Action**: Always respect developer habits and session checkpoints injected via `claude-mem`. If `claude-mem` is unavailable (e.g. in Kilo, OpenClaw, OpenCode, Claude Desktop), fall back to active chat session history and local scratchpads (e.g. active notes or current document) to maintain short-term state.
   - **Purpose**: Maintain task continuity and follow local guidelines for the active coding session.

2. **L1: Structural Memory (Codebase Topology)**
   - **Action**: ALWAYS prefer `codebase-memory-mcp` graph tools over grep/glob/file-search for code discovery. Use them in this priority order:
     1. `search_graph` — find functions, classes, routes, variables by pattern
     2. `trace_path` — trace who calls a function or what it calls
     3. `get_code_snippet` — read specific function/class source code
     4. `query_graph` — run Cypher queries for complex patterns
     5. `get_architecture` — high-level project summary
   - **Fallback to grep/glob** only when: searching string literals/error messages, non-code files (Dockerfiles, shell scripts), or when MCP tools return insufficient results.
   - **Purpose**: Map out upstream/downstream module dependencies and community structures to prevent architectural regressions.

3. **L2: Long-Term Memory (Historical Memory Palace)**
   - **Action**: Before answering queries about system design, past debugging history, environment setups, or business logic, proactively query the memory database using:
     - `mempalace_search` — semantic full-text search across all memories
     - `mempalace_kg_query` — structured knowledge graph query
     - `mempalace_traverse` — traverse connected entities
     - `mempalace_get_drawer` — retrieve a specific memory by ID
   - **Purpose**: Leverage persistent historical context to avoid repeating past errors or reinventing existing patterns.
