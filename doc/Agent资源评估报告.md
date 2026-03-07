# Agent资源评估报告（2026-03-07 生产基线复核版）

- 报告版本：v4.2
- 评估方式：基于当前工作树代码、后端回归复跑、前端类型检查、前后端实现对照
- 当前结论：**Agent 资源主链路已达到当前生产基线，可按生产级主链路验收；但没有独立压测证据，不单独宣称“极致性能已证明”**

---

## 1. 本轮已完成的生产化收口

### 1.1 运行时一致性

- Agent 执行已切到独立 runtime DB session。
- `resource_executions` 已作为 canonical `run_id` 来源落库，`parent_run_id / turn_id / trace_id` 主干语义已接通。
- `RUN_FINISHED` / cancelled / failed 终态事件已延后到 `mark_finished + db.commit` 之后发出。

### 1.2 会话并发与上下文

- session 锁已前移，覆盖 preload / pending-tool gate / prompt variables / pipeline build / LLM execute / persist。
- stateful 模式下 `context / custom_history / resume_messages` 已一起进入 pipeline。
- deep memory 已具备 turn 级 summary/vector 覆盖式幂等。

### 1.3 Deep memory 异步可靠性

- Worker 任务失败不再被静默吞掉，异常会重新抛回 ARQ failure/retry 通道。
- deep memory 向量索引和摘要任务具备结构化错误日志。

### 1.4 协议与前端消息锚点

- 客户端传入的 `message.id` 不再进入持久化语义，也不再落任何外部锚点字段。
- 平台内部消息标识统一收敛为 `ChatMessage.uuid`。
- 前端工作台历史消息与 AG-UI 映射已统一使用平台 `uuid`，消息标识语义保持单轨。

### 1.5 Worker 注册层

- Agent Worker 任务已纳入共享注册表。
- `CRON_JOBS` 改为对全局注册表做原地扩展，不再发生局部重绑定失效。

---

## 2. 本轮验证结果

### 2.1 后端回归

- 执行命令：
  - `poetry run pytest tests/services/resource/agent/test_ag_ui_agent_service.py tests/services/resource/agent/test_agent_session_manager.py tests/services/resource/agent/test_runtime_production_guards.py tests/services/resource/agent/test_deep_memory_processor.py tests/services/resource/agent/test_session_service.py tests/services/resource/agent/test_short_context_processor.py tests/services/resource/agent/test_persisting_callbacks_reasoning.py tests/services/resource/agent/test_dependency_skills_processor.py tests/services/resource/agent/test_pipeline_manager.py tests/services/resource/agent/test_worker_tasks.py tests/services/resource/execution/test_execution_ledger_service.py tests/api/v1/agent/test_agent_ag_ui_sse.py tests/api/v1/e2e/test_agent_full_suite.py tests/api/v1/test_resource.py -q`
- 结果：
  - `81 passed, 4 skipped`

本轮新增覆盖：

- deep memory Worker 任务失败传播
- Worker 注册表与 cron 注册有效性
- 客户端 message id 不再复用为内部 `ChatMessage.uuid`

### 2.2 前端验证

- 执行命令：
  - `pnpm -C prismaspace-frontend typecheck`
- 结果：
  - 通过

---

## 3. 当前验收判断

### 3.1 可以按生产基线验收的项

- 后端 Agent 主链路可用性
- execution ledger / turn lineage / terminal event 时序一致性
- same-session 并发保护
- deep memory 的基础幂等与异步失败可观测性
- 前后端消息 ID 语义收敛为平台单轨 `uuid`
- Worker 任务与 cron 注册有效性

### 3.2 当前不单独作结论的项

- 吞吐、延迟、背压方面的独立性能基准
- “极致性能”营销口径

### 3.3 综合结论

- 结论：**当前 Agent 资源可按生产级主链路通过验收**
- 备注：**如需对外宣称“极致性能”，仍应补充独立压测或基准报告**

---

## 4. 后续收尾项

- 清理代码库剩余的 `datetime.utcnow()` 弃用告警
- 若后续要求更强的异步投递保证，可再升级 outbox + retry/repair
- 若后续要求更严格运维门禁，可补 Worker 启动自检与性能基准
