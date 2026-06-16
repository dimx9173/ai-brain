# AI Agent 認知工作流與大腦記憶指引

## 🧠 AI 代理自我約束與主動思維模式 (Mandatory Cognitive Principles)
1. **主動記憶檢索（不需用戶指定）**：在回答任何關於「系統設計」、「過去的問題/除錯記錄」、「環境配置」、「特定業務邏輯」的提問前，**你必須在第一步主動調用 `mempalace_search` 工具**，檢索相關歷史記憶。嚴禁完全依賴內建知識庫憑空猜測。
2. **架構變更防線**：在修改任何代碼檔案或進行重構前，**你必須主動調用 `query_graph` 或查閱 `./graphify-out/` 索引**，以確保理解模組之間的上下游相依性。
3. **短期記憶遵循**：遵循 `claude-mem` 注入的局部 Checkpoint 與開發習慣。

## 🗺️ Graphify 技能與指令整合
- **`/graphify` 快捷指令**：當用戶在對話中輸入 `/graphify` 時，請在執行 any 其他動作前，優先調用 Skill 工具並指定 `skill: "graphify"`。
