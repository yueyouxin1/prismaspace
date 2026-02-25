import logging
import copy
from typing import List, Dict, Any, Union, Optional, Set
from ...utils.tokenizer.manager import tokenizer_manager

logger = logging.getLogger(__name__)

class LLMContextManager:
    """
    [Production-Ready] 智能 LLM 上下文管理器
    
    核心策略：
    1. 原子性 (Atomicity): 严格保证 Assistant Call 与 Tool Result 同生共死，避免 API 报错。
    2. 锚点保护 (Anchoring): 始终保留 System Prompt 和 最后一轮对话。
    3. 动态压缩 (Dynamic Compression): 仅在总 Budget 不足时，才去压缩历史记录中的 Tool Output，
       最大程度利用 Context Window。
    """
    
    def __init__(self):
        pass

    def _get_tokenizer(self, provider: str, model: str):
        return tokenizer_manager.get_tokenizer(provider, model)

    def _get_attr(self, obj: Any, key: str, default: Any = None):
        """兼容 Pydantic v1/v2 和 Dict 的属性获取"""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _count_text_tokens(self, text: str, tokenizer) -> int:
        if not text: return 0
        return len(tokenizer.encode(str(text)))

    def _count_msg_tokens(self, msg: Any, tokenizer) -> int:
        """计算单条消息 Token (包含 overhead 估算)"""
        num_tokens = 4  # 基础协议开销
        
        content = self._get_attr(msg, 'content')
        if content:
            num_tokens += self._count_text_tokens(content, tokenizer)
        
        tool_calls = self._get_attr(msg, 'tool_calls')
        if tool_calls:
            for tool_call in tool_calls:
                func = self._get_attr(tool_call, 'function') or tool_call.get('function', {})
                if func:
                    f_name = self._get_attr(func, 'name') or func.get('name', '')
                    f_args = self._get_attr(func, 'arguments') or func.get('arguments', '')
                    num_tokens += self._count_text_tokens(f_name, tokenizer)
                    num_tokens += self._count_text_tokens(f_args, tokenizer)
        return num_tokens

    def _clone_and_update_msg(self, msg: Any, new_content: str) -> Any:
        """克隆消息并更新内容，保持原始对象类型"""
        if hasattr(msg, 'model_copy'):
            # Pydantic v2
            return msg.model_copy(update={"content": new_content})
        elif hasattr(msg, 'copy'):
            # Pydantic v1 or Dict
            new_msg = msg.copy()
            if isinstance(new_msg, dict):
                new_msg['content'] = new_content
            else:
                # Pydantic v1 usually supports copy but fields might be immutable, 
                # strictly speaking model_copy is safer for pydantic.
                # Here we assume if it's object, we try setattr for fallback
                try:
                    setattr(new_msg, 'content', new_content)
                except Exception:
                    # Fallback for immutable objects: return dict representation implies losing class type
                    # In strict usage, rely on Pydantic models being properly defined.
                    pass 
            return new_msg
        else:
            # Fallback for unknown objects: try deepcopy
            new_msg = copy.deepcopy(msg)
            try:
                setattr(new_msg, 'content', new_content)
            except:
                logger.warning(f"Could not update content for message type {type(msg)}")
            return new_msg

    def _compress_text(self, text: str, tokenizer, target_tokens: int) -> str:
        """智能截断文本，保留头尾"""
        tokens = tokenizer.encode(str(text))
        if len(tokens) <= target_tokens:
            return text
        
        # 至少保留头尾各一点，避免 target_tokens 过小
        keep_each_side = max(10, target_tokens // 2)
        if keep_each_side * 2 > len(tokens):
             return text # 无法压缩更多
            
        head = tokenizer.decode(tokens[:keep_each_side])
        tail = tokenizer.decode(tokens[-keep_each_side:])
        return f"{head}\n...[Content Compressed: {len(tokens)-target_tokens} tokens hidden]...\n{tail}"

    def _group_into_turns(self, messages: List[Any]) -> List[List[Any]]:
        """
        [关键逻辑] 将消息列表转化为原子对话轮次 (Turns)。
        规则：Assistant (with tool_calls) 必须和后续的 Tool (results) 绑定在一起。
        """
        turns = []
        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            role = self._get_attr(msg, 'role')
            tool_calls = self._get_attr(msg, 'tool_calls')
            
            # 只有 Assistant 发起调用时，才启动“粘滞”模式，吸附后续的 tool 消息
            if role == "assistant" and tool_calls:
                current_turn = [msg]
                j = i + 1
                while j < n:
                    next_msg = messages[j]
                    next_role = self._get_attr(next_msg, 'role')
                    if next_role == "tool":
                        current_turn.append(next_msg)
                        j += 1
                    else:
                        break
                turns.append(current_turn)
                i = j 
            else:
                turns.append([msg])
                i += 1
        return turns

    def manage(
        self,
        messages: List[Any],
        provider: str,
        model: str,
        max_context_tokens: int,
        reserve_tokens: int = 500
    ) -> List[Any]:
        """
        主入口。
        :param messages: 消息列表 (Dict 或 Pydantic)
        :param model: 模型名称
        :param max_context_tokens: 允许的最大上下文 Token 数
        :param reserve_tokens: 额外预留的安全空间
        """
        if not messages: return []
        
        tokenizer = self._get_tokenizer(provider, model)
        budget = max_context_tokens - reserve_tokens
        
        # 1. 转化为原子轮次
        turns = self._group_into_turns(messages)
        if not turns: return []

        # 2. 识别必须保留的锚点 (System + Last Turn)
        # 使用 Set 防止只有一条消息时重复添加
        kept_indices: Set[int] = set()
        kept_indices.add(len(turns) - 1) 
        
        if self._get_attr(turns[0][0], 'role') == 'system':
            kept_indices.add(0)
        
        # 3. 计算锚点开销 & 构建基础结果
        current_tokens = 0
        final_turns_map: Dict[int, List[Any]] = {}
        
        for idx in kept_indices:
            turn = turns[idx]
            turn_cost = sum(self._count_msg_tokens(m, tokenizer) for m in turn)
            final_turns_map[idx] = turn
            current_tokens += turn_cost
        
        # 极端情况防御：仅锚点就超标 (例如 Last Turn 的 Tool Output 极大)
        if current_tokens > budget:
            logger.warning("System Prompt + Last Turn exceeds budget. Performing aggressive compression on Last Turn.")
            # 对 Last Turn 中的 Tool Output 进行强力压缩
            last_idx = len(turns) - 1
            if last_idx in final_turns_map:
                compressed_turn = []
                for msg in final_turns_map[last_idx]:
                    if self._get_attr(msg, 'role') == 'tool':
                        # 强行压缩到很小，比如 500 tokens
                        content = self._get_attr(msg, 'content') or ""
                        new_content = self._compress_text(content, tokenizer, 500)
                        compressed_turn.append(self._clone_and_update_msg(msg, new_content))
                    else:
                        compressed_turn.append(msg)
                final_turns_map[last_idx] = compressed_turn
                # 重新计算 token (略过，为了性能假设它变小了)
            
            # 返回最小集合
            return [msg for idx in sorted(kept_indices) for msg in final_turns_map[idx]]

        # 4. 历史回填 (从倒数第二轮开始向前)
        # 逻辑：对于每一轮，先看完整放入是否超标。
        # 如果超标，且该轮包含 Tool 消息，尝试压缩 Tool 消息后再放入。
        # 如果还放不下，则停止。
        
        for i in range(len(turns) - 2, -1, -1):
            if i in kept_indices: continue
            
            raw_turn = turns[i]
            
            # 预计算
            turn_cost_full = 0
            tool_indices = []
            non_tool_cost = 0
            
            for m_idx, msg in enumerate(raw_turn):
                cost = self._count_msg_tokens(msg, tokenizer)
                turn_cost_full += cost
                if self._get_attr(msg, 'role') == 'tool':
                    tool_indices.append(m_idx)
                else:
                    non_tool_cost += cost
            
            # 策略 A: 完整放入
            if current_tokens + turn_cost_full <= budget:
                final_turns_map[i] = raw_turn
                current_tokens += turn_cost_full
                continue
            
            # 策略 B: 动态压缩放入 (仅当存在 Tool 消息时)
            if tool_indices:
                remaining_budget = budget - current_tokens
                
                # 如果连非 Tool 部分 (Assistant问句等) 都放不下，那这轮没法要了
                if non_tool_cost >= remaining_budget:
                    break 
                
                # 计算可分配给 Tool 的额度
                available_for_tools = remaining_budget - non_tool_cost
                # 设置一个最小阈值，如果每个 tool 分不到 100 token，压缩意义不大，直接丢弃
                if available_for_tools < 100 * len(tool_indices):
                    break
                
                token_per_tool = available_for_tools // len(tool_indices)
                
                # 构建压缩后的轮次
                squeezed_turn = list(raw_turn) # 浅拷贝列表结构
                actual_squeezed_cost = non_tool_cost
                
                for t_idx in tool_indices:
                    original_msg = squeezed_turn[t_idx]
                    content = self._get_attr(original_msg, 'content') or ""
                    
                    # 压缩
                    new_content = self._compress_text(content, tokenizer, token_per_tool)
                    new_msg = self._clone_and_update_msg(original_msg, new_content)
                    
                    squeezed_turn[t_idx] = new_msg
                    actual_squeezed_cost += self._count_msg_tokens(new_msg, tokenizer)
                
                # 双重检查 (防止估算误差)
                if current_tokens + actual_squeezed_cost <= budget:
                    final_turns_map[i] = squeezed_turn
                    current_tokens += actual_squeezed_cost
                else:
                    break # 即使压缩也放不下
            else:
                # 纯文本对话放不下，停止
                break

        # 5. 组装并按原始顺序返回
        final_messages = []
        for i in sorted(final_turns_map.keys()):
            final_messages.extend(final_turns_map[i])
            
        return final_messages