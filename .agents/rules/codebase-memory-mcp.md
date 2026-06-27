---
trigger: always_on
description: Consult the codebase-memory-mcp knowledge graph for codebase and architecture questions.
---

## codebase-memory-mcp

This project uses `codebase-memory-mcp` to maintain a knowledge graph of the codebase.

Rules:
- For codebase or architecture questions, first run codebase-memory-mcp tools like `search_graph`, `trace_path`, `get_code_snippet`, `query_graph`, or `get_architecture`.
- Prefer querying the codebase-memory-mcp knowledge graph for structural information over standard file-by-file search/reading or raw grep output.
