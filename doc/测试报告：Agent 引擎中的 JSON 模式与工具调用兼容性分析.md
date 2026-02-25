这是一个关于 Agent 引擎与 LLM 结构化输出模式（JSON Mode）兼容性的测试报告。

请将此文档作为 **核心开发文档** 或 **最佳实践指南** 存档，以警示后续开发者避免陷入常见的配置陷阱。

---

# 📑 测试报告：Agent 引擎中的 JSON 模式与工具调用兼容性分析

**日期**：2026-01
**测试对象**：AgentEngineService (ReAct Loop)
**测试模型**：Qwen-plus-2025-09-11 (兼容 OpenAI 协议)
**风险等级**：🔴 **高 (Critical)**

## 1. 核心结论 (TL;DR)

**严禁**在 Agent 引擎的 `run_config` 中同时启用 `tools`（工具定义）和 `response_format={"type": "json_object"}`（原生 JSON 模式）。

这样做会导致模型**跳过工具调用步骤（Short-circuiting）**，直接生成包含幻觉数据的 JSON 结果。

**推荐方案**：保持默认的文本模式运行 Agent，通过 **System Prompt** 约束最终输出格式，并配合**代码层面的 JSON 解析器**进行提取。

---

## 2. 测试场景与结果

我们对比了三种不同的配置策略，旨在让 Agent 完成一个需要使用工具（Tool Use）才能回答的问题（例如：查询股票价格、获取秘密Token）。

### ❌ 场景 A：启用原生 JSON 模式 (Native JSON Mode)
*   **配置**：`response_format={"type": "json_object"}` 或 `json_schema`
*   **预期**：Agent 先调用工具获取数据，最后以 JSON 格式输出。
*   **实际结果**：
    *   **步骤数 (Steps)**：`0` (未执行任何工具调用)
    *   **输出**：模型直接编造了数据并输出了 JSON（幻觉）。
    *   **原因**：API 级别的 JSON 约束优先级极高。模型接收到“必须输出 JSON”的指令后，为了满足格式要求，抑制了“调用工具”的推理逻辑。

### ❌ 场景 B：原生 JSON 模式 + 强制工具选择 (Tool Choice Required)
*   **配置**：`response_format={"type": "json_object"}`, `tool_choice="required"`
*   **实际结果**：
    *   **结果**：虽然强制发生了工具调用，但由于 Agent 引擎通常使用静态配置，导致模型陷入死循环（反复调用工具）或在工具返回后依然无法正确切换回 JSON 生成模式。
    *   **结论**：配置复杂且极不稳定，不推荐生产使用。

### ✅ 场景 C：提示词驱动模式 (Prompt-Driven Mode)
*   **配置**：`response_format=None` (默认文本模式)
*   **Prompt**：在系统提示词中明确要求 "Final output must be JSON"。
*   **实际结果**：
    *   **步骤数 (Steps)**：`2+` (正常执行了思考、工具调用、观察)
    *   **输出**：Agent 在获取工具结果后，能够遵循 System Prompt 将最终答案整理为合法的 JSON 格式。
    *   **结论**：**这是唯一可行且稳定的方案。**

---

## 3. 错误示范 vs 正确示范

### 🚫 错误示范 (Anti-Pattern)

```python
# 警告：不要这样做！Agent 将失去行动能力。
run_config = LLMRunConfig(
    model="qwen-plus",
    tools=my_tools,  # 定义了工具
    response_format={"type": "json_object"}  # ❌ 同时开启了 JSON 模式
)
```

**后果**：
> `[Agent]` Total Steps: 0
> `[Output]` {"price": 100}  <-- 这是模型瞎编的，根本没查数据库

---

### ✅ 正确示范 (Best Practice)

#### 第一步：配置 (保持默认)

```python
# 正确：让模型在自由文本模式下思考和行动
run_config = LLMRunConfig(
    model="qwen-plus",
    tools=my_tools,
    response_format=None  # ✅ 关闭原生 JSON 模式
)
```

#### 第二步：Prompt 设计

```python
system_prompt = """
你是一个数据助手。
任务流程：
1. 接收请求。
2. 调用工具获取真实数据。
3. 最终输出：仅输出一个有效的 JSON 对象。不要包含 Markdown 标记。

JSON 格式示例：
{
    "data": "...",
    "source": "tool_name"
}
"""
```

#### 第三步：健壮的解析器 (Robust Parser)

由于模型可能会输出 ` ```json ... ``` `，必须使用辅助函数进行清洗：

```python
import json
import re

def parse_agent_json(text: str) -> dict:
    """清洗并解析 Agent 输出的 JSON"""
    try:
        # 1. 尝试直接解析
        return json.loads(text)
    except json.JSONDecodeError:
        # 2. 提取 Markdown 代码块
        pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        match = re.search(pattern, text)
        if match:
            return json.loads(match.group(1))
        # 3. 提取首尾花括号
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            return json.loads(text[start:end+1])
        raise ValueError("无法从 Agent 输出中提取 JSON")
```

---

## 4. 深度原理分析

为什么 Agent 引擎与 JSON 模式互斥？

1.  **ReAct 循环机制**：Agent 的本质是 `Thought -> Action -> Observation -> Thought`。这是一个**动态的、多步骤的**文本生成过程。
2.  **JSON 模式的刚性**：`response_format="json_object"` 是对模型单次输出的**全局约束**。它要求输出的**每一个 Token** 都在构建一个 JSON 对象。
3.  **冲突**：工具调用请求（Function Call）在底层协议中通常表现为特殊的 XML 标记或特定的 API 结构，这**不符合**用户预定义的最终 JSON 业务结构。当开启 JSON 模式时，模型被强迫去填充业务 JSON，从而“忘记”了它本该先发出工具调用请求。

## 5. 建议行动

1.  **检查代码库**：搜索所有 `AgentEngineService.run()` 的调用处。
2.  **移除配置**：确保没有同时传入 `tools` 和 `response_format`。
3.  **工具库集成**：将 `parse_agent_json` 实用函数添加到项目的 `src/app/utils/` 目录中，供所有 Agent 结果处理逻辑使用。