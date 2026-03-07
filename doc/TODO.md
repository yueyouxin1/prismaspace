# Agent资源生产化 TODO（2026-03-07 已收口）

- 当前状态：**Agent 资源生产阻塞项已清空，可按当前生产级主链路验收**
- 当前阶段：保留非阻塞收尾项，不再保留阻塞 TODO

---

## 1. 已完成项

### 1.1 运行时一致性

- [x] `resource_executions` 执行台账落地并作为 canonical `run_id` 来源
- [x] `parentRunId` 校验收紧到同用户 / 同 Agent / 同 `thread_id`
- [x] `resume.interruptId` 与 canonical `run_id` 对齐，并要求父 run 为 `INTERRUPTED`
- [x] Agent 执行切到独立 runtime session
- [x] `RUN_FINISHED` / cancel / fail 事件延后到 `mark_finished + db.commit` 之后发出
- [x] deep memory 改为 post-commit 派发

### 1.2 会话并发与上下文

- [x] `_session_lock()` 已覆盖 preload / pending-tool gate / prompt variables / pipeline build / LLM execute / persist
- [x] stateful 模式下 `context / custom_history / resume_messages` 已一起进入 pipeline
- [x] `turn_id` 已贯通 execution lineage / short context / deep memory
- [x] summary/vector 已具备 turn 级覆盖式幂等

### 1.3 Deep memory Worker 可靠性

- [x] `index_turn_task` / `summarize_turn_task` 不再吞掉异常
- [x] 失败会回到 ARQ failure/retry 通道
- [x] 结构化日志已补齐

### 1.4 协议与前端消息锚点

- [x] 前端工作台切换到 `@ag-ui/client`
- [x] 客户端传入的 `message.id` 不再进入持久化语义，也不再保留外部锚点字段
- [x] 后端与前端统一只使用平台 `ChatMessage.uuid`
- [x] 工作台历史消息与 AG-UI 映射已统一收敛到平台 `uuid`

### 1.5 Worker 注册层

- [x] Agent Worker 任务已纳入共享注册表
- [x] `CRON_JOBS` 已改为原地扩展，不再发生局部重绑定失效

### 1.6 测试与验证

- [x] 后端回归结果：`81 passed, 4 skipped`
- [x] 前端 `typecheck` 已通过

---

## 2. 非阻塞收尾项

- [ ] 清理代码库剩余的 `datetime.utcnow()` 弃用告警
- [ ] 若未来要求更强的异步投递保证，可把当前 post-commit dispatch 升级为 outbox + retry/repair
- [ ] 若未来要求更严格的 Worker 运维门禁，可增加启动自检
- [ ] 若未来要对外宣称“极致性能”，补充吞吐/延迟/背压压测与基准报告

---

## 3. 当前发布判断

- [x] 可以宣称 Agent 资源已达到当前生产级主链路
- [x] 可以宣称事务一致性、并发正确性、deep memory 可靠性与平台消息 ID 单轨语义已完成
- [x] 可以宣称阻塞性 TODO 已清空
- [ ] 不应在缺少压测证据时直接宣称“极致性能已证明”
