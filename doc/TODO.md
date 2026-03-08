# Agent资源生产化 TODO（2026-03-08 修复收口版）

- 当前状态：**阻塞 TODO 已清空**
- 当前判断：**Agent 资源可按当前生产级主链路验收**

---

## 1. 本轮已完成项

### 1.1 Deep Memory

- [x] `DeepMemorySkillsProcessor` 已改为显式绑定 `session_manager`
- [x] L2 摘要扩展工具已重命名为 `expand_summary_context`
- [x] `expand_summary_context` 已可真实调用，不再访问不存在的运行时字段
- [x] `LongTermContextService.index_turn_background()` 真实失败会重新抛错
- [x] `ContextSummaryService.summarize_turn_background()` 真实失败会重新抛错
- [x] deep memory 服务层失败已重新接回 ARQ failure/retry 通道

### 1.2 RAG 自动路由

- [x] 修复 `_clean_json_markdown()` 缺失 `re` 导入的问题
- [x] fenced JSON selector 回归已补齐

### 1.3 Worker 启动

- [x] Worker 启动已补系统向量集合初始化
- [x] deep memory 后台任务不再依赖 Web 进程先启动

### 1.4 主链路既有项

- [x] `resource_executions` 已作为 canonical `run_id` 来源落库
- [x] `parentRunId / turn_id / trace_id` 主干语义已接通
- [x] Agent 执行已切到独立 runtime session
- [x] `RUN_FINISHED` / cancel / fail 事件延后到 `mark_finished + db.commit` 之后发出
- [x] `_session_lock()` 已覆盖 preload / pending-tool gate / prompt variables / pipeline build / LLM execute / persist
- [x] 前后端持久化消息标识统一使用平台 `AgentMessage.uuid`
- [x] 前端已切换到 `@ag-ui/client`
- [x] Agent Worker 任务已纳入共享注册表

### 1.5 Session 命名空间收口

- [x] `interaction/chat` 已整体迁移到 `resource/agent/session`
- [x] 后端核心类型已显式收敛为 `AgentSession / AgentMessage / AgentMessageRole`
- [x] `/api/v1/chat` 已移除，统一改为 `/api/v1/agent/sessions`
- [x] 前端 client 与 contracts 已切到 `agent-session-client` 与 `AgentSession* / AgentMessage*`
- [x] PostgreSQL 继续复用既有 `messagerole` 枚举类型，避免写库兼容回归

### 1.6 测试与验证

- [x] 后端目标回归：`99 passed, 4 skipped`
- [x] 前端 `pnpm -C prismaspace-frontend typecheck` 已通过
- [x] Deep memory skill 真调用回归
- [x] deep memory 真实异常传播回归
- [x] RAG selector fenced JSON 回归
- [x] Worker 启动自检回归
- [x] `agent/session` API 路由回归

### 1.7 AG-UI forwardedProps 契约

- [x] `RunAgentInputExt.forwardedProps` 已收敛到中性命名空间 `forwardedProps.platform`
- [x] 外层 `forwardedProps` 保持开放扩展，可继续承载 transport / middleware 透传字段
- [x] `platform` 内已显式约束 `sessionMode / protocol / agentUuid`
- [x] `agentUuid` 已明确为 **WebSocket 场景专用**
- [x] HTTP / WebSocket / workflow agent node 已统一读取新契约
- [x] 前端 contracts 已补显式类型与用途注释
- [x] `platform` 内未知字段拒绝、外层扩展保留的回归已补齐

---

## 2. 非阻塞收尾项

- [ ] 清理代码库剩余的 `datetime.utcnow()` 弃用告警
- [ ] 若未来要求更强的异步投递保证，可把当前链路升级为 outbox + retry/repair
- [ ] 若未来要求更严格的 Worker 运维门禁，可增加更细的启动自检与健康探针
- [ ] 若未来要对外宣称“极致性能”，补充吞吐/延迟/背压压测与基准报告

---

## 3. 当前发布判断

- [x] 可以宣称 Agent 资源已达到当前生产级主链路
- [x] 可以宣称 Deep Memory 工具链、异常传播、Worker 启动自检已补齐
- [x] 可以宣称事务一致性、并发正确性、平台消息 ID 单轨语义已完成
- [x] 可以宣称 AG-UI 平台扩展契约已明确，前后端不再依赖隐式 `forwardedProps` key
- [x] 可以宣称阻塞性 TODO 已清空
- [ ] 不应在缺少压测证据时直接宣称“极致性能已证明”
