# LangGraph vs Coze Workflow vs PrismaSpace 自研引擎评估报告

## 1. 结论先行

这次问题有两个：

1. `reference_example\langgraph\libs` 对比 Coze 开源 Workflow 与本项目，自身是否更优秀？
2. 我们是否适合放弃自研引擎，全面切到 LangGraph 集成，以快速达到生产水准？

最终结论分两层：

### 结论 A：LangGraph 是否“优于 Coze/workflow”

**不能简单下“LangGraph 全面优于 Coze/workflow”的结论。**

更准确的说法是：

- **在“状态化 agent runtime / durable execution / interrupt-resume / checkpoint”这个维度，LangGraph 的抽象更纯、通用性更强，很多内核能力甚至强于 Coze。**
- **在“产品化 Workflow 平台 / 低代码画布 / 资源编排 / 版本治理 / 节点生态 / OpenAPI / ChatFlow / 执行历史产品面”这个维度，Coze 明显更完整。**
- **在“当前本项目的执行器能力”这个维度，LangGraph 显著强于本项目。**

因此：

- **LangGraph > 本项目自研引擎**
- **LangGraph 不等于全面 > Coze Workflow**
- **LangGraph 与 Coze 的强项不在同一层**

### 结论 B：我们是否应该放弃自研引擎，全面切到 LangGraph

**不建议。**

至少不建议以“全面替换”的方式做。

更推荐的结论是：

- **不应全面放弃现有应用层/资源层/工作流平台层，自研部分仍然需要保留。**
- **可以有选择地引入 LangGraph，作为新的 agent-oriented runtime 或长时任务 runtime。**
- **最优路线不是“全量切换”，而是“混合架构：平台层保留，执行层局部替换/增强”。**

一句话总结：

**LangGraph 适合补强我们，不适合一键取代我们。**

---

## 2. 评估范围

本次基于本地源码评估以下范围：

### LangGraph

- `reference_example/langgraph/libs/langgraph`
- `reference_example/langgraph/libs/checkpoint`
- `reference_example/langgraph/libs/checkpoint-postgres`
- `reference_example/langgraph/libs/checkpoint-sqlite`
- `reference_example/langgraph/libs/prebuilt`
- `reference_example/langgraph/libs/cli`
- `reference_example/langgraph/libs/sdk-py`

### Coze 开源

- `reference_example/coze-studio/backend/application/workflow`
- `reference_example/coze-studio/backend/domain/workflow/service`
- `reference_example/coze-studio/backend/domain/workflow/internal/compose`
- `reference_example/coze-studio/backend/domain/workflow/internal/execute`
- `reference_example/coze-studio/backend/domain/workflow/internal/schema`
- `reference_example/coze-studio/backend/domain/workflow/internal/nodes`
- `reference_example/coze-studio/backend/domain/workflow/internal/repo`

### 本项目

- `src/app/api/v1/workflow`
- `src/app/services/resource/workflow`
- `src/app/engine/workflow`
- `src/app/system/resource/workflow/node_def_manager.py`

说明：

- 对 Coze 与本项目的基础分析沿用前一次源码阅读结果，但本报告完全独立成文。
- 本报告重点关注“执行内核、运行治理、生产能力、替换成本”，而不是 UI 细节。

---

## 3. 三者的本质定位

## 3.1 LangGraph

**一个面向 stateful agent / long-running workflow 的低层 orchestration runtime。**

从本地 README 和源码看，它的中心不是“低代码工作流平台”，而是：

- code-first graph builder
- shared state / channels
- Pregel/BSP 风格调度
- checkpoint
- interrupt / resume
- threads / state history
- subgraph persistence
- tool-calling agent loop

它更接近：

- “可持久化的 agent runtime”
- “stateful graph execution kernel”

而不是：

- Coze 那样的产品化 Workflow 平台
- 本项目当前这种 DB 驱动节点模板 + 前端画布 + 服务层执行器

## 3.2 Coze Workflow

**一个 workflow 产品平台。**

它不只是 runtime，还覆盖：

- 画布 DSL
- 节点元数据
- 版本和草稿
- 执行历史
- 节点调试
- ChatFlow
- 资源引用
- 搜索索引事件
- 对话与业务平台耦合

## 3.3 本项目

**一个轻量工作流服务 + 内嵌执行器。**

当前更像：

- DB 驱动的 workflow instance
- 基于 JSON graph 的内存执行
- 面向当前产品需求的简洁工作流系统

---

## 4. LangGraph 架构概览

## 4.1 核心构成

从源码结构看，LangGraph 本地 `libs` 主要由以下部分组成：

### 1. 核心执行库

- `langgraph/graph/state.py`
- `langgraph/pregel/main.py`
- `langgraph/pregel/_loop.py`
- `langgraph/types.py`
- `langgraph/channels/*`

这部分定义了：

- `StateGraph`
- `Pregel`
- channel 模型
- state reducer
- retry / cache / interrupt / command / send

### 2. 持久化与 checkpoint

- `checkpoint`
- `checkpoint-postgres`
- `checkpoint-sqlite`
- `checkpoint-conformance`

这部分定义了：

- checkpointer 标准接口
- pending writes
- thread/checkpoint 模型
- Postgres / SQLite 实现
- conformance spec

### 3. 高层 agent 预制件

- `prebuilt/chat_agent_executor.py`
- `prebuilt/tool_node.py`

这部分提供：

- ReAct agent loop
- ToolNode
- ValidationNode
- agent interrupt patterns

### 4. 部署与运行配套

- `cli`
- `sdk-py`

这部分提供：

- `langgraph dev`
- `langgraph up`
- `langgraph build`
- 远程运行/线程/助手 SDK

注意：

- 这说明 LangGraph 不是只有库，没有运行链路。
- 但它的完整生产部署故事，明显部分依赖 LangGraph API server / LangSmith Deployment 体系。

---

## 4.2 执行模型

LangGraph 的执行核心不是“遍历 DAG 节点”，而是：

- graph compile
- channels
- triggers
- superstep execution
- checkpointed loop

也就是更接近一个 **通用状态机 runtime**。

### 执行核心特征

#### 1. Shared state + reducer

`StateGraph` 的节点签名是：

- `State -> Partial<State>`

每个 state key 可配置 reducer，这一点非常重要。

这意味着：

- 并行节点可以安全聚合状态
- map-reduce 风格很自然
- 多分支汇聚不是“手工 merge dict”而是 channel/reducer 语义

#### 2. Pregel/BSP 风格 superstep

`pregel/main.py` 和 `pregel/_loop.py` 明确体现：

- 按 step 规划要执行的 actors
- 当前 step 内写入对同 step 不可见
- step 结束统一 apply writes
- 然后进入下一 step

这比本项目当前的“任务队列 + ready 节点 gather”更接近成熟运行时模型。

#### 3. 节点是“读 channel / 写 channel”的 actor

而不是简单的 `execute(node, context)`。

这种设计的好处是：

- 并发语义更明确
- streaming / updates / debug 都能统一建模
- 中间状态天然可持久化

#### 4. 原生 Command / Send / Interrupt

`langgraph.types` 提供：

- `Send`
- `Command`
- `interrupt()`
- `Interrupt`
- `StateSnapshot`

这些不是后加的工具函数，而是 runtime 一等公民。

这意味着：

- 分支跳转
- 并行 fan-out
- HITL
- resume

都是框架原生能力。

---

## 4.3 持久化模型

LangGraph 的 production 核心优势几乎都来自 checkpoint 体系。

### 关键点

- checkpoint 保存 graph state snapshot
- 每个 thread 按 `thread_id` 组织
- 可从指定 `checkpoint_id` 恢复
- 每个 superstep 后写 checkpoint
- 节点失败时支持 pending writes，避免重复跑已成功节点

这比本项目当前只靠内存 `WorkflowContext` 的方式高一个级别。

同时，checkpoint 还有：

- `BaseCheckpointSaver` 接口
- async/sync 双版本
- Postgres/SQLite 实现
- conformance test suite

这个设计非常成熟。

从“运行内核正确性”上说，LangGraph 在这部分是本次对比里最强的一方之一。

---

## 4.4 流式、调试、状态历史

LangGraph 的 `stream_mode` 支持：

- `values`
- `updates`
- `checkpoints`
- `tasks`
- `debug`
- `messages`
- `custom`

这意味着它不只是“支持 SSE 输出”，而是：

- 支持按不同抽象层输出
- 支持 state history
- 支持 task start/finish/result/error
- 支持子图流式输出
- 支持 messages token 级事件

这在框架设计上明显强于本项目，也不弱于 Coze。

---

## 5. 与 Coze 的对比

## 5.1 谁的 runtime 抽象更强

如果只看执行内核抽象，我认为：

**LangGraph 的 runtime 抽象比 Coze 更纯。**

原因：

- LangGraph 明确是一个图执行内核
- state/channel/reducer/interrupt/checkpoint 是同一套理论体系
- 代码形态更接近“通用框架”
- 业务耦合非常低

而 Coze 的 workflow runtime 虽然成熟，但它更像：

- 产品平台内核
- 带大量业务语义和历史包袱的工程系统

所以从“内核抽象优雅度”看：

- **LangGraph > Coze**

但这里必须立刻补一句：

**抽象更纯，不等于平台能力更完整。**

## 5.2 谁的产品化能力更强

这个维度反过来：

- Coze 明显更强

Coze 有：

- 画布 DSL 与后端 schema 转换
- NodeTypeMeta / 节点元数据体系
- workflow meta / draft / version
- workflow execution / node execution 查询
- node debug
- ChatFlow
- workflow as tool
- 外部资源依赖
- 搜索索引事件
- 对话/知识/插件/数据库节点生态

LangGraph 本地 `libs` 并没有提供这些产品层能力。

它提供的是：

- 执行内核
- checkpoint
- agent 预制件
- CLI/SDK/部署链路

换句话说：

- **Coze 是平台**
- **LangGraph 是 runtime framework**

## 5.3 谁更适合“低代码工作流平台”

**Coze 更适合。**

因为低代码平台需要：

- 可视化节点目录
- 编辑器 DSL
- 节点配置表单
- 节点级产品语义
- 资源/版本/权限治理
- 调试和执行记录产品面

LangGraph 本地代码主路径是 code-first，不是 DB/DSL-first。

它并不天然适合“让业务用户在画布上拖节点”这种产品模型。

## 5.4 谁更适合“复杂 agent orchestration”

**LangGraph 更强，至少更原生。**

因为它对以下场景支持非常自然：

- 多步 agent loop
- state accumulation
- thread-based memory
- interrupt / resume
- subgraph persistence
- code-first dynamic routing
- parallel sends
- reducer-driven merge

Coze 当然也能做，但 Coze 的出发点不是“agent runtime framework”。

### 结论

如果问：

- `LangGraph 是否优于 Coze/workflow？`

答案是：

- **作为通用 stateful agent runtime，LangGraph 很可能优于 Coze。**
- **作为完整 workflow 平台，LangGraph 不优于 Coze。**
- **如果不区分层次直接说“LangGraph 优于 Coze/workflow”，这个结论是不成立的。**

---

## 6. 与本项目的对比

## 6.1 内核成熟度

这一点没有悬念：

**LangGraph 明显强于本项目当前引擎。**

本项目当前引擎的核心特征：

- `WorkflowGraphDef` + `WorkflowGraph`
- `WorkflowOrchestrator`
- `WorkflowContext`
- `NodeRegistry`
- 进程内 `asyncio.Task` 调度

它能跑、也清晰，但本质仍是一个轻量执行器。

而 LangGraph 有：

- compile 后的 runtime
- channels/reducers
- checkpoint
- thread/checkpoint_id
- state history
- interrupt/resume
- subgraph persistence
- retry/cache/durability mode
- task/debug stream

### 这意味着

- **LangGraph 在“可恢复执行”上远强于本项目**
- **LangGraph 在“并发状态聚合语义”上远强于本项目**
- **LangGraph 在“生产级状态治理”上远强于本项目**

## 6.2 易理解性

这里反而是本项目更强。

本项目：

- 结构更短
- 逻辑更直
- API -> Service -> Orchestrator 清楚

LangGraph：

- 抽象层很多
- `StateGraph` / `Pregel` / `Loop` / `Checkpoint` / `Channel` / `Command`
- 学习成本和排障成本都更高

所以：

- **可理解性：本项目 > LangGraph**
- **成熟 runtime 能力：LangGraph > 本项目**

## 6.3 扩展简单节点

本项目当前加简单节点非常快：

- `NodeTemplate`
- `@register_node`
- `execute()`
- `sync_nodes()`

LangGraph 加简单节点也不难，但它是 code-first graph，不是 node catalog-first。

因此：

- 如果目标是“扩一个内部节点给工作流画布用”，本项目更顺手。
- 如果目标是“写一个状态化 agent graph / runtime behavior”，LangGraph 更强。

## 6.4 与当前代码库的语言/技术栈适配

这里 LangGraph 有一个重要优势：

- **它是 Python**

相比 Coze 的 Go：

- 语言迁移成本低很多
- 可以直接复用我们现有的 Python service、agent、tool、schema
- LLM / Agent / Tool 封装迁移到 LangGraph 节点更容易

这也是为什么：

- **LangGraph 很适合作为“补强我们引擎”的候选项**
- 但仍然 **不等于适合全面替换**

---

## 7. 生产能力评估

## 7.1 LangGraph 的生产强项

### 1. Durable execution

这几乎是 LangGraph 的第一卖点。

它在本地 README 和代码里都很明确：

- checkpoint at every superstep
- thread_id / checkpoint_id
- pending writes
- interrupt/resume
- subgraph persistence

这点远强于本项目当前实现。

### 2. Human-in-the-loop

`interrupt()` + `Command(resume=...)` 是原生能力。

这不是简单的“暂停任务”，而是：

- 节点内部抛出可恢复的 interrupt
- graph state 持久化
- 恢复时从节点起点重跑，并恢复 resume data

这个设计非常成熟。

### 3. Checkpoint 后端可替换

本地代码已有：

- memory
- sqlite
- postgres
- conformance suite

这说明它不是“玩具 checkpoint”，而是认真设计过的 persistence contract。

### 4. State history / update_state / fork from checkpoint

从测试可见，LangGraph 支持：

- `get_state()`
- `get_state_history()`
- `update_state()`
- 从历史 checkpoint 继续运行

这类能力对生产排障、人工修复、审计都很有价值。

### 5. 配套运行方式存在

虽然本地没看到完整 control plane 源码，但已有：

- CLI
- Docker build/up
- SDK threads/runs/assistants API

说明它不是只有本地库，没有运行体系。

## 7.2 LangGraph 的生产短板

### 1. 它解决的是 runtime，不是整个平台

LangGraph 可以帮你达到：

- agent runtime production level

但它不能自动给你：

- workflow 产品平台
- 低代码节点管理
- 资源版本发布体系
- 节点 UI 表单元数据
- 多租户资源权限模型
- 产品级执行历史查询面板

这些仍然要你自己做。

### 2. 部分“生产部署”明显依赖外部平台生态

README 里反复强调：

- LangSmith
- LangGraph Studio
- LangSmith Deployment

这意味着它的最佳生产体验，很大程度不是单靠本地 libs 就能得到。

如果我们不采用它们的外部平台，只拿本地 libs：

- 仍然需要自己做很多平台层工作

### 3. code-first 与我们 current model 冲突

LangGraph 的主路径是：

- 在代码里声明 graph

而我们当前的主路径是：

- DB 持久化 graph JSON
- 前端画布编辑
- 节点模板从后端同步

这不是小差异，而是范式差异。

### 4. 对团队工程能力要求更高

LangGraph 不是黑盒。

要把它真正用到生产，而不是 demo，需要团队理解：

- state schema
- reducer
- thread/checkpoint 语义
- interrupt/resume 生命周期
- subgraph 边界
- stream modes
- LangChain Runnable 语义

对团队要求比当前本项目高。

---

## 8. 是否适合“全面切换”

我的结论是：**不适合。**

## 8.1 为什么不适合全面切换

### 原因 1：替换的不只是引擎，而是整个抽象模型

我们现在系统的核心抽象是：

- Workflow 实例
- 节点模板 DB
- 图 JSON
- 引用同步
- 服务层执行

如果全面切到 LangGraph，就不是换个 `run()` 实现那么简单，而是要重做：

- graph IR
- 节点定义方式
- 前端 DSL 到 runtime 的编译器
- Stream/interrupt 对接
- execution persistence 模型
- state thread 映射

### 原因 2：LangGraph 不是低代码引擎

如果我们的长期目标仍然是：

- 可视化 workflow 平台
- 节点模板生态
- 资源选择器
- DB 驱动流程

那 LangGraph 不是现成答案。

它更适合：

- 开发者写 graph
- 开发者组合 agent

### 原因 3：迁移成本极高，短期收益未必匹配

全面替换意味着：

- 现有节点库要重写
- Loop / Branch / End / Tool / Agent 语义要重新映射
- 所有 SSE/WS 事件协议要重做
- 执行历史和 trace 对齐要重做
- 现有 workflow graph 数据要迁移或双栈兼容

这不是“接入一个依赖包”，而是一次架构迁移。

### 原因 4：达到生产水准，不只是 runtime 的问题

生产水准 = 运行时能力 + 平台能力 + 运维能力。

LangGraph 可以补 runtime，但：

- 产品层
- 资源层
- 权限层
- 版本发布层
- 节点 catalog 层

还得我们自己做。

所以“为了快速达到生产水准而全面切换”这个前提，本身就不成立。

---

## 8.2 什么情况下适合引入 LangGraph

### 情况 1：我们要强化 Agent Runtime

如果我们未来更重视：

- 多步 agent
- human approval
- tool loop
- long-running stateful task
- checkpoint recovery

那 LangGraph 很合适。

### 情况 2：我们接受“部分 workflow 改成 code-first”

例如：

- 内部 agent 流程
- 专项复杂长任务
- 研发自定义 pipeline

而不是要求所有流程都来自拖拽画布。

### 情况 3：我们愿意做编译层，而不是直接替换平台层

更现实的方式是：

- 保留现有 Workflow 平台模型
- 新增一个中间 IR
- 将部分 workflow/agent DSL 编译到 LangGraph
- 用 LangGraph 做 runtime

这就把 LangGraph 放在“执行层”而不是“平台层”。

---

## 9. 推荐策略

## 9.1 不推荐：全面切换

**不推荐。**

原因：

- 风险大
- 周期长
- 迁移对象太多
- 不能自动补齐平台层能力

## 9.2 推荐：混合架构

**强烈推荐。**

推荐形态：

### 平台层保留

保留我们现有的：

- Workflow 资源模型
- 节点模板同步
- 权限/引用/版本服务
- API/SSE/WS 网关
- DB 中的 workflow instance 和 graph

### 执行层做“双 runtime”

按能力分流：

- 简单流程：继续走现有自研引擎
- 复杂 agent / 长时任务 / HITL 流程：走 LangGraph runtime

### 先做“小范围编译器”

先支持一小类节点编译到 LangGraph：

- LLMNode
- AgentNode
- ToolNode
- Branch
- 简单 Loop / fan-out

不要一开始就把整个 workflow 体系搬过去。

## 9.3 推荐：先把 LangGraph 用在 AgentNode / Tool orchestration

这是最现实的一步。

因为这类节点：

- 与 LangGraph 的强项最匹配
- 与我们的现有系统边界也最清晰
- 可以最快体现 durable execution / interrupt/resume 价值

而不是先去替换 Start/End/Loop/画布层。

---

## 10. 三方评分

以下评分针对不同维度，不代表“总体胜负”。

| 维度 | LangGraph | Coze | 本项目 |
|---|---:|---:|---:|
| 通用 runtime 抽象 | 9.5 | 8 | 5 |
| durable execution | 9.5 | 8.5 | 3 |
| interrupt / resume | 9.5 | 8.5 | 2 |
| code-first agent orchestration | 9.5 | 7 | 5 |
| 低代码 workflow 平台能力 | 4 | 9.5 | 5 |
| 节点 catalog / 模板 / UI 驱动 | 3 | 9 | 7 |
| 资源与版本治理 | 4 | 9.5 | 5 |
| 本地轻量可读性 | 6 | 4 | 8 |
| 对当前代码库直接适配性 | 7 | 3 | 10 |
| 作为本项目增强方案的价值 | 9 | 6 | - |

解读：

- **LangGraph 是最强 runtime framework。**
- **Coze 是最强 workflow 平台。**
- **本项目是当前最贴合我们现状的系统，但 runtime 能力最弱。**

---

## 11. 客观证据

一些能说明成熟度的客观信号：

### LangGraph

- 核心 runtime 文件规模：
  - `langgraph/graph/state.py` 1731 行
  - `langgraph/pregel/main.py` 3345 行
  - `langgraph/pregel/_loop.py` 1328 行
  - `prebuilt/chat_agent_executor.py` 1015 行
- `langgraph/langgraph` 下 Python 核心文件约 65 个
- `langgraph/tests` 下测试文件约 39 个
- `prebuilt/tests` 下测试文件约 16 个
- 有独立 checkpoint 包、postgres/sqlite 实现和 conformance suite

### Coze

- 执行服务核心：
  - `domain/workflow/service/executable_impl.go` 1111 行
  - `domain/workflow/internal/compose/workflow.go` 894 行
- 节点实现与平台层能力远超 LangGraph
- 但强业务耦合更高

### 本项目

- `workflow_service.py` 901 行
- `orchestrator.py` 437 行
- Workflow 专项测试仍较薄，主要覆盖 API 运行和少量节点能力

---

## 12. 对“是否放弃自研引擎”的明确回答

## 答案

**否。**

至少现阶段不应该：

- 放弃现有 workflow 平台层
- 全面切 LangGraph
- 把“达到生产水准”的希望押在一次全面迁移上

## 更合理的回答

**应该放弃“继续只靠当前自研 runtime 演进到生产级 durable execution”的幻想。**

也就是说：

- 不是继续单靠当前引擎硬扛
- 也不是一刀切切到 LangGraph
- 而是要引入更成熟的 runtime 思路

最合适的方案是：

**保留我们的平台层，自研 IR，局部引入 LangGraph 作为执行内核。**

---

## 13. 最终建议

### 建议 1

继续保留现有：

- Workflow API
- Resource/Instance 模型
- NodeDefManager
- 节点模板/表单同步
- 业务权限与引用关系

### 建议 2

新增一层 Runtime IR，不要直接把现有 graph JSON 喂给任意引擎。

### 建议 3

以试点方式引入 LangGraph，优先覆盖：

- AgentNode
- LLMNode
- ToolNode
- HITL 场景
- 长时流程

### 建议 4

不要把低代码画布、workflow catalog、版本系统一起推倒重来。

### 建议 5

如果未来确认产品方向转向“开发者构建 agent 平台”，再考虑提高 LangGraph 在整体架构中的权重。

---

## 14. 最终一句话

**LangGraph 是比我们当前自研引擎强得多的 agent/workflow runtime，但它不是 Coze 那种完整 workflow 平台，也不是我们现有系统的一键替代品。**

**因此，不建议全面切换；建议混合引入，把 LangGraph 用作执行层增强，而不是平台层替代。**

