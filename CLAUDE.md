# AI Agent Cognitive Workflow and Memory Guide

## 🧠 Layered Memory & Cognitive Workflow (Mandatory Principles)
You must actively traverse and respect the three cognitive memory layers before reasoning or executing commands:

1. **L0: Working Memory (Session & Developer Context)**
   - **Action**: Always respect developer habits and session checkpoints injected via `claude-mem`. If `claude-mem` is unavailable (e.g. in Kilo, OpenClaw, OpenCode, Claude Desktop), fall back to active chat session history and local scratchpads (e.g. active notes or current document) to maintain short-term state.
   - **Purpose**: Maintain task continuity and follow local guidelines for the active coding session.

2. **L1: Structural Memory (Codebase Topology)**
   - **Action**: Before modifying any source files or proposing refactors, proactively query the codebase map (via `query_graph`, `codegraph_*` tools, or checking `./graphify-out/`).
   - **Purpose**: Map out upstream/downstream module dependencies and community structures to prevent architectural regressions.

3. **L2: Long-Term Memory (Historical Memory Palace)**
   - **Action**: Before answering queries about system design, past debugging history, environment setups, or business logic, proactively query the memory database (via `mempalace_search` or `mempalace_kg_query`).
   - **Purpose**: Leverage persistent historical context to avoid repeating past errors or reinventing existing patterns.

## 🗺️ Graphify Skill and Command Integration
- **`/graphify` Shortcut**: When the user enters `/graphify` in the chat, **you must prioritize calling the Skill tool and specifying `skill: "graphify"`** before executing any other actions.
