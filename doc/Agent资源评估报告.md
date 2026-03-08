# Agent资源评估报告（2026-03-08 forwardedProps契约收口版）

- 报告版本：v5.3
- 评估方式：基于当前工作树代码审查、问题修复、最小复现回归、目标回归测试、前端类型检查
- 当前结论：**Agent 资源当前已达到生产基线，可按生产级主链路验收；但仍没有独立压测证据，不单独宣称“极致性能已证明”。**

---

## 1. 本轮已完成的生产化修复

### 1.1 Deep Memory 能力闭环

- `DeepMemorySkillsProcessor` 已改为显式依赖 `session_manager`，只在存在持久会话时注册 L2 摘要扩展工具。
- Deep memory 摘要扩展工具已收敛为 L2 语义，工具名调整为 `expand_summary_context`。
- `expand_summary_context` 现在可正确按 `session_uuid + agent_instance_id + turn_id` 回查完整轮次。
- 不再存在运行时访问不存在 `session_manager` 的问题。

### 1.2 Deep Memory 异步失败传播

- `LongTermContextService.index_turn_background()` 在真实失败时已重新抛错。
- `ContextSummaryService.summarize_turn_background()` 在真实失败时已重新抛错。
- Deep memory 服务层异常现已重新接回 `worker/tasks/agent.py` 的 ARQ failure/retry 通道。

### 1.3 RAG 自动路由健壮性

- `RAGContextProcessor._clean_json_markdown()` 已补齐 `re` 依赖。
- fenced JSON 返回不再触发 `NameError`，自动路由不会因为实现缺陷退化为全量知识库召回。

### 1.4 Worker 冷启动自检

- Worker 启动现在会主动执行系统向量集合初始化。
- deep memory 索引与摘要任务不再依赖 Web 进程先启动完成向量系统预热。

### 1.5 无效代码清理

- 已移除长期记忆索引路径里的未使用变量，减少无效实现痕迹。

### 1.6 Session 命名空间收口

- `interaction/chat` 模型、DAO、schema 与 API 已整体迁移到 `resource/agent/session`。
- 会话与消息核心类型已显式收敛为 `AgentSession / AgentMessage / AgentMessageRole`。
- 会话 API 已统一到 `/api/v1/agent/sessions`，不再保留 `/api/v1/chat` 兼容层。
- 前端请求与 contracts 已同步切到 `agent-session-client` 与 `AgentSession* / AgentMessage*` 命名。
- PostgreSQL 枚举类型继续复用既有 `messagerole`，避免命名迁移引发写库兼容问题。

### 1.7 AG-UI forwardedProps 契约收口

- `RunAgentInputExt.forwardedProps` 已补显式契约，采用中性命名空间 `forwardedProps.platform`，不再依赖项目名前缀。
- 外层 `forwardedProps` 仍保持开放扩展，允许 transport / middleware 透传额外字段。
- `forwardedProps.platform` 现已收敛为强校验平台契约：
  - `sessionMode`: `auto | stateless | stateful`
  - `protocol`: 当前仅支持 `ag-ui`
  - `agentUuid`: **仅 WebSocket 场景使用**，用于 transport routing
- `platform` 内未知字段现会被拒绝，避免前端和调用方继续无约束乱传。
- HTTP / WebSocket / workflow agent node 对 `sessionMode`、`protocol`、`agentUuid` 的读取已统一切到该契约。

---

## 2. 已核实成立的生产化项

### 2.1 运行时一致性

- Agent 执行已切到独立 runtime DB session。
- `resource_executions` 已作为 canonical `run_id` 来源落库。
- `parent_run_id / turn_id / trace_id` 主干语义已打通。
- `RUN_FINISHED` / cancelled / failed 终态事件仍然在 `mark_finished + db.commit` 之后发出。

### 2.2 会话并发与上下文

- session 锁已覆盖 preload / pending-tool gate / prompt variables / pipeline build / LLM execute / persist。
- stateful 模式下 `context / custom_history / resume_messages` 已进入 pipeline。
- Deep memory 的 summary/vector 路径现在具备真实异常传播，不再只停留在 wrapper 级“看起来会抛错”。

### 2.3 协议与前端消息锚点

- 前后端持久化消息主键语义已收敛到平台 `AgentMessage.uuid`。
- 客户端传入的 `message.id` 未进入持久化锚点。
- 前端工作台已切到 `@ag-ui/client`，历史消息与流式消息均以平台 `uuid` 为准。
- `forwardedProps.platform` 现已成为唯一平台保留扩展入口；其中 `agentUuid` 已明确标注为 WebSocket 专用字段。

### 2.4 Worker 注册与启动

- Agent Worker 任务已进入共享注册表，`CRON_JOBS` 仍为原地扩展。
- Worker 启动现已包含向量系统集合自检，deep memory 后台链路具备独立启动能力。

---

## 3. 本轮验证结果

### 3.1 后端回归

- 执行命令：
- `poetry run pytest tests/services/resource/agent/test_ag_ui_agent_service.py tests/services/resource/agent/test_agent_session_manager.py tests/services/resource/agent/test_runtime_production_guards.py tests/services/resource/agent/test_deep_memory_processor.py tests/services/resource/agent/test_session_service.py tests/services/resource/agent/test_short_context_processor.py tests/services/resource/agent/test_persisting_callbacks_reasoning.py tests/services/resource/agent/test_dependency_skills_processor.py tests/services/resource/agent/test_pipeline_manager.py tests/services/resource/agent/test_worker_tasks.py tests/services/resource/execution/test_execution_ledger_service.py tests/api/v1/agent/test_agent_ag_ui_sse.py tests/api/v1/agent/test_agent_ws_handler.py tests/api/v1/agent/test_agent_session_api.py tests/api/v1/e2e/test_agent_full_suite.py tests/api/v1/test_resource.py -q`
- 结果：
  - `99 passed, 4 skipped`
  - `108 warnings`

本轮新增回归覆盖：

- `DeepMemorySkillsProcessor` 的真实工具注册与调用
- deep memory 服务真实异常传播
- RAG selector fenced JSON 清洗
- Worker 启动时的系统向量集合初始化
- `agent/session` API 路由与服务绑定
- `forwardedProps.platform` 契约校验、开放外层扩展与 WebSocket 专用 `agentUuid` 解析

### 3.2 前端验证

- 执行命令：
  - `pnpm -C prismaspace-frontend typecheck`
- 结果：
  - 通过

---

## 4. 当前验收判断

### 4.1 可以按生产基线验收的项

- Agent 主执行链路
- execution ledger / turn lineage / terminal event 时序一致性
- same-session 并发保护
- Deep memory 的工具可用性、后台异常传播与 Worker 独立启动自检
- RAG auto selector 的 fenced JSON 基础健壮性
- 平台消息 `uuid` 单轨语义
- `agent/session` 命名空间边界
- AG-UI `forwardedProps.platform` 契约边界与 WebSocket 专用 `agentUuid`
- Worker 注册表与前端 `@ag-ui/client` 接入

### 4.2 当前不单独作结论的项

- 吞吐、延迟、背压方面的独立性能基准
- “极致性能”营销口径

### 4.3 综合结论

- 结论：**当前 Agent 资源可按生产级主链路通过验收。**
- 备注：**如需对外宣称“极致性能”，仍应补充独立压测或基准报告。**

---

## 5. 后续收尾项

- 清理代码库剩余的 `datetime.utcnow()` 弃用告警
- 若未来要求更强的异步投递保证，可继续升级 outbox + retry/repair
- 若未来要求更严格的 Worker 运维门禁，可补更多启动自检与健康探针
- 若未来要对外宣称“极致性能”，补充吞吐/延迟/背压压测与基准报告
