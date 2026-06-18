# AI Agent Cognitive Workflow and Memory Guide

## 🧠 AI Agent Self-Constraint & Active Thinking (Mandatory Cognitive Principles)
1. **Active Memory Retrieval (No User Instruction Required)**: Before answering any questions regarding "system design", "past issues/debugging history", "environment configuration", or "specific business logic", **you must proactively call the `mempalace_search` tool as your first step** to retrieve relevant historical memories. Relying solely on internal knowledge bases and guessing is strictly prohibited.
2. **Architecture Change Protection**: Before modifying any code files or performing refactoring, **you must proactively call `query_graph` or inspect `./graphify-out/` index** to ensure you understand the upstream and downstream dependencies between modules.
3. **Short-Term Memory Compliance**: Adhere to local checkpoints and developer habits injected by `claude-mem`.

## 🗺️ Graphify Skill and Command Integration
- **`/graphify` Shortcut**: When the user enters `/graphify` in the chat, **you must prioritize calling the Skill tool and specifying `skill: "graphify"`** before executing any other actions.
