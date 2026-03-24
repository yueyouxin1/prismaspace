# Resource Runtime技术文档（2026-03-24）

- 文档版本：v1.0
- 适用范围：PrismaSpace `resource runtime` 通用执行层，当前重点覆盖 `Agent` 与 `Workflow`
- 文档目标：统一运行时心智、统一热路径优先级、统一数据分层原则、统一代码架构标准
- 当前结论：**Resource Runtime 应以“首事件路径效率极致”和“用户尽快收到流”为第一优先级；`Agent` 已接近目标形态，`Workflow` 应向 `Agent` 收敛，而不是继续保留历史分叉心智。**

---

## 1. 设计目标

Resource Runtime 的核心目标不是“把所有执行信息尽可能多地记录下来”，而是：

- 让用户尽快收到首个事件与后续流
- 让运行中断开/重连体验足够好
- 让执行结果、恢复能力、事后查询与回放都具备生产可用性
- 让不同 Resource 类型在运行时层面采用统一心智与统一架构

运行时设计必须优先满足下面两个原则：

1. 流热路径优先  
   运行中高频事件不能被数据库等重 I/O 阻塞。

2. 语义分层清晰  
   live、replay、checkpoint、trace、execution 各自有清晰职责，不做职责重叠和重复记录。

---

## 2. 总体判断

### 2.1 Agent 与 Workflow 的关系

`Agent` 和 `Workflow` 的流场景在运行时本质上非常接近：

- 都会产生高频中间事件
- 都有 live attach / reconnect 的需求
- 都有 run 查询、事后回放、失败恢复的需求

因此，两者应该共享同一套运行时心智：

- `live` 是短生命周期、best-effort 的实时观察通道
- `replay` 是长生命周期、durable 的事后查询通道
- 热路径优先发给用户，再考虑事后持久化

`Agent` 当前更接近这一目标。`Workflow` 的后续演进原则应当是：

- **优先向 Agent 对齐**
- **尽量不反向把 Workflow 的历史复杂度带回 Agent**

### 2.2 当前方向判断

当前应明确采用以下方向：

- `Workflow` 流事件热路径对齐 `Agent`
- `Workflow` callbacks / persistence / query / control / execution 分层对齐 `Agent`
- 所有 Resource Runtime 统一采用“热路径 emit + 事后批量持久化”的默认模型

---

## 3. Runtime 数据分层

运行时相关数据应拆成四层，各层职责必须明确。

### 3.0 当前 writer / reader 矩阵

| 层 | 主要 writer | 主要 reader | 当前职责判断 |
| --- | --- | --- | --- |
| `executions` | `ExecutionLedgerService` | `run_query` / `run_control` / API summary | 合理，保持轻量 run 主记录 |
| `checkpoints` | `runtime_persistence` / durable observer | `run_preparation`（resume）、`run_query`（latest checkpoint） | 继续评估粒度中；成功态 checkpoint 已开始压缩 |
| `events` | `persisting_callbacks` captured + `run_persistence` terminal batch append | `/events`、`/replay`、interrupt 解析 | 已收敛为 durable timeline，方向正确 |
| `traces` | `TraceManager` root span + node interceptor | auditing API / 内部观测 | 职责独立，不应承担用户态 replay |
| `node executions` | `runtime_persistence` observer | `WorkflowRunRead` 详情页 / 排障视图 | 有价值，但需继续评估字段冗余 |

当前总体判断：

- `executions` 与 `traces` 的职责边界相对清晰
- `events` 已开始向“最小回放周期”收敛
- `checkpoints` 与 `node executions` 仍是下一轮最值得评估的冗余热点

关于冗余的当前约束判断：

- `trace` 的冗余不能只从“存储优化”视角判断
- 只要这种冗余换来 trace 的独立观测能力，就具有合理性
- 因此，当前阶段不建议直接把 `trace` 改造成仅引用业务事实层的投影

当前已确认的真实消费面：

- `trace_id`
  - 被 Workflow Workbench 运行详情页直接展示
  - 同时作为 auditing trace 查询入口的桥接键
- `latest_checkpoint`
  - 被 Workflow Workbench 运行详情页直接展示和格式化输出
  - 也是 resume 能力的入口元信息
- `node_executions`
  - 被 Workflow Workbench 运行详情页直接展示
  - 不只是 demo mock 数据，实际 workbench 组件存在真实消费

因此，现阶段更合理的优化方向是：

- **压缩和收敛其内部持久化粒度**
- **而不是直接删除这些返回字段或取消这些视图**

### 3.1 Executions

用途：

- 作为 run 主记录
- 表达执行状态、主索引、错误信息、起止时间

应存内容：

- `run_id`
- `thread_id`
- `trace_id`
- `status`
- `started_at`
- `finished_at`
- `error_code`
- `error_message`

不应承担的职责：

- 不存详细事件流
- 不存可恢复快照
- 不承担流重连

### 3.2 Checkpoints

用途：

- 只为恢复执行服务

应存内容：

- resume 所需的最小状态快照
- 必要的 runtime snapshot / pending state

不应承担的职责：

- 不作为 timeline
- 不作为用户态事件回放来源
- 不记录所有运行细节

原则：

- checkpoint 频率必须克制
- 只保留恢复真正需要的信息

当前收敛方向：

- `execution_succeeded` checkpoint 不再保存完整 resume 级 state
- 成功态 checkpoint 只保留紧凑 terminal 视图，供 latest checkpoint 查询面使用
- 失败 / 中断 / 取消等可恢复场景仍保留完整恢复所需 state

### 3.3 Events

用途：

- 事后 timeline
- replay
- 用户态运行详情查询

事件存储原则：

- 不应存 token/chunk 级别所有高频中间事件
- 应仅存“对最小回放周期有价值”的事件

建议保留的 durable event：

- `run.started`
- `node.started`
- `stream.started`
- 低频 progress / snapshot 类事件（可选）
- `stream.finished`
- `node.completed`
- `node.failed`
- `run.finished`
- `run.failed`
- `run.interrupted`
- `run.cancelled`

原则：

- durable event log 服务“可理解的回放”
- 不服务 transport 级逐 token 镜像

### 3.4 Traces

用途：

- 内部性能观测
- 执行拓扑
- 调试与审计辅助

核心原则：

- `trace` 必须保持观测自主性
- `trace` 的输入/输出/错误快照应以 trace 在当时采集到的数据为准
- `trace` 不应简单退化成对 `node_executions`、`events` 或其他业务事实层的被动引用

这意味着：

- `trace` 与业务事实层可以存在一定冗余
- 这种冗余在当前阶段是可接受的，因为它换来了更高的调试可信度和上下文独立性
- 后续若要减重，应优先考虑“自治摘要化 / 截断化 / 采样化”，而不是取消 trace 的自主记录能力

原则：

- trace 不承担用户态 replay 职责
- trace 与 event 不应重复承担同一职责
- trace 可按需要采样、降级、截断

---

## 4. Live / Replay 分层原则

### 4.1 Live

`live` 的职责是：

- 支撑运行中的实时观察
- 支撑断连/重连后短期重新接流

`live` 的技术定义：

- 短生命周期
- best-effort
- 主要依赖进程内 buffer / Redis 短期缓冲

`live` 的销毁原则：

- run 结束后自然终止
- 不应长期依赖 DB fallback 补流

结论：

- `stream_live_events` 不应承担 durable replay 责任
- `stream_live_events` 不值得为用户体验增强点引入复杂 DB fallback

### 4.2 Replay

`replay` 的职责是：

- 基于 durable event log 做事后回放
- 面向已完成或已落库的事件历史

结论：

- `replay` 只应依赖 durable event store
- 不应混入 live buffer 的短期语义

### 4.3 两者关系

统一心智如下：

- `live` = 实时通道
- `replay` = 事后通道
- 两者接口可以相似，但语义不能混淆

---

## 5. 热路径原则

### 5.1 统一热路径模型

默认模型应为：

1. 事件先进入 live sink
2. 事件立即进入对外 generator
3. 同时在内存中 captured
4. run 到达 terminal 后批量落 durable store

这个模型的直接收益：

- 首事件更快
- 高频中间事件不会被 DB I/O 拖慢
- 运行时热路径更可预测

### 5.2 禁止项

以下逻辑默认不应出现在流热路径：

- 每事件 DB flush
- 每 token Redis 远程查询
- 与回放无关的同步持久化
- 与用户当前流无关的额外元数据查询

---

## 6. 代码架构统一标准

Agent 已经基本形成较清晰的运行时分层，Workflow 应向其收敛。

### 6.1 建议统一的文件职责

每类 Resource Runtime 应优先具备以下模块：

- `run_preparation.py`
  - 请求校验
  - run 上下文准备
  - 恢复前置逻辑

- `run_execution.py`
  - 后台执行编排
  - runtime observer / callback / task wiring

- `run_query.py`
  - run 查询
  - replay / live attach 查询面

- `run_control.py`
  - cancel / active-run / control 面

- `run_persistence.py`
  - run 级 durable persistence facade

- `live_events.py`
  - live buffer / detach / reconnect

- `persisting_callbacks.py`
  - 热路径 emit
  - captured events
  - 协议事件适配

### 6.2 不推荐的形态

不推荐继续保留：

- callbacks 塞在 `run_execution.py`
- 上层服务直接依赖底层 event log service
- 运行中热路径、持久化路径、协议适配路径混在一个文件

### 6.3 命名统一原则

命名应尽量采用统一模式：

- `PersistingXxxCallbacks`
- `XxxRunExecutionService`
- `XxxRunQueryService`
- `XxxRunPersistenceService`
- `XxxLiveEventService`

避免：

- 同一职责在不同 Resource 中采用完全不同的命名
- facade/service/manager 边界不清

---

## 7. Workflow 对齐 Agent 的落地方向

### 7.1 第一阶段

目标：

- 流事件热路径对齐 Agent
- callbacks 抽离
- 每事件 DB 持久化改为 terminal 后批量持久化

收益：

- 首事件路径变短
- 高频流事件时延下降
- 架构职责清晰

### 7.2 第二阶段

目标：

- `Workflow` 进一步统一 `run_persistence` / `run_query` / `live_events` 心智
- 评估并收敛 `stream_live_run_events` 的 DB fallback

原则：

- 若目标是和 Agent 完全统一，则 live attach 只保留 live buffer 语义
- durable replay 走 `/events` 与 `/replay`

### 7.3 第三阶段

目标：

- 统一 Resource Runtime 抽象边界
- 尽可能提炼跨 Resource 通用模式，但避免过早抽象

原则：

- 先统一心智和文件职责
- 再抽公共基建

---

## 8. 事件持久化粒度建议

### 8.1 Durable event 的建议保留粒度

建议 durable event 以“阶段事件”为主，而不是 chunk 级事件。

可以考虑的持久化策略：

- 默认不持久化所有 `stream.delta`
- 仅对 terminal 流节点保存最终内容和必要摘要
- 若确需回放过程，可增加稀疏 snapshot 事件，而不是逐 chunk 记录

### 8.2 可接受的折中策略

可选策略包括：

- 完全忽略 chunk 级 durable event
- 固定时间窗口抽样
- 固定字符增量阈值采样
- 仅记录关键节点的流快照

选择标准：

- 是否显著提升 replay 价值
- 是否能控制数据量增长
- 是否会明显拖慢热路径

---

## 9. 对现有机制的评估原则

当前系统已存在：

- `executions`
- `checkpoints`
- `events`
- `traces`
- `node executions`

后续评估必须回答以下问题：

1. 这层数据是否有唯一明确职责？
2. 是否与其他层重复记录相同信息？
3. 是否真实被查询、恢复或回放逻辑使用？
4. 热路径中写入这层数据是否值得？

如果某层不满足以上要求，应考虑：

- 削减
- 降频
- 迁移到异步路径
- 与其他层合并

---

## 10. 当前统一结论

本项目 Resource Runtime 的统一原则应明确为：

- **用户尽快收到流 > 高频事件逐条 durable 持久化**
- **live 是短生命周期实时通道，不做重 DB fallback**
- **replay 是 durable 事后通道，不承载实时语义**
- **event log 只保留对最小回放周期有价值的事件**
- **executions / checkpoints / events / traces 必须职责分离**
- **Workflow 应持续向 Agent 对齐，Agent 不应回退到 Workflow 的历史复杂度**

---

## 11. 当前落地状态

### 11.1 已落地

当前已经完成的收口包括：

- `Agent` durable event 改为显式白名单，只保留最小回放周期有价值的事件
- `Workflow` 流热路径改为：
  - live sink
  - generator emit
  - 内存 captured
  - terminal 后批量 durable persistence
- `Workflow` 已新增独立 `persisting_callbacks.py`
- `Workflow` 已新增 `run_persistence.py` facade
- `Workflow` live attach 已收口为 live buffer 语义，不再依赖 DB fallback
- `Workflow` durable event 改为显式白名单，不再默认记录 `stream.delta` 这类 chunk 级高频事件

### 11.2 待落地

仍建议继续推进的项：

- 进一步评估 durable event 的最小保留集合
- 评估 `executions / checkpoints / events / traces` 的冗余与职责边界
- 继续把 `Workflow` 的上层调用全部统一收口到 `run_persistence_service`
- 视需要再决定是否把底层 event log 实现进一步内聚进 `run_persistence.py`

---

## 12. 后续建议

建议后续按以下顺序推进：

1. 完成 Workflow 流热路径对齐 Agent
2. 清理 Workflow live attach 的 DB fallback 历史逻辑
3. 收敛 Workflow durable event 粒度，移除 chunk 级持久化
4. 评估 `executions / checkpoints / events / traces` 的冗余与边界
5. 再考虑抽象跨 Resource 的公共 runtime 基建

---

## 13. 附：一句话心智模型

`live` 解决“现在看”，`replay` 解决“之后查”，`checkpoint` 解决“失败恢复”，`trace` 解决“内部观测”，`execution` 解决“run 主状态”。

任何一层职责不清，都会同时损伤性能和可维护性。
