# src/app/core/trace_manager.py

import time
import uuid
import logging
import contextvars
import traceback
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Type, Callable, Awaitable
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.auditing import Trace, TraceStatus
from app.services.auditing.types.attributes import BaseTraceAttributes

logger = logging.getLogger(__name__)

# ==============================================================================
# Context Variables (The Invisible Nervous System)
# ==============================================================================

# 1. 全局 Trace ID (贯穿整个 Request/Task)
_ctx_trace_id = contextvars.ContextVar("trace_id", default=None)

# 2. 用户上下文 (Who)
_ctx_user_id = contextvars.ContextVar("user_id", default=None)

# 3. 活跃实例上下文 (Who called whom?)
# 当前正在执行的 Resource Instance ID (作为下一层调用的 Source)
_ctx_active_instance_id = contextvars.ContextVar("active_instance_id", default=None)

# 4. 业务容器上下文 (Anchor)
# (context_type, context_id) 自动遗传给子孙
_ctx_business_context = contextvars.ContextVar("business_context", default=(None, None))

# 5. [核心] 父节点栈 (Topology)
# 存储 parent_span_uuid 的栈。Stack Top 就是当前的 Parent。
_ctx_span_stack = contextvars.ContextVar("span_stack", default=[])

# 6. [核心] 写入缓冲区 (Performance)
# 仅在根节点初始化。所有子节点产生的 Trace 对象都 append 到这里。
_ctx_buffer = contextvars.ContextVar("trace_buffer", default=None)

# 通用的生命周期钩子机制
_ctx_flush_hooks = contextvars.ContextVar("trace_flush_hooks", default=None)

class TraceManager:
    """
    高性能、优雅的 Trace 上下文管理器。
    
    Features:
    - 自动父子关系推导 (基于 Stack)
    - 自动 Source/Target 推导 (基于 Context)
    - 延迟批量写入 (Deferred Batch Persistence)
    - 强类型 Attributes 支持
    """
    
    def __init__(
        self, 
        db: AsyncSession,
        operation_name: str,
        # --- 核心参数 ---
        user_id: Optional[int] = None, # 显式指定用户（根节点必传）
        force_trace_id: Optional[str] = None, # 显式指定 TraceID（如业务层已生成）
        attributes: BaseTraceAttributes = None,
        # --- 拓扑参数 (可选，通常自动推导) ---
        target_instance_id: Optional[int] = None, # 本次被调用的对象
        source_instance_id: Optional[int] = None, # 发起者 (不传则自动取 active_instance)
        # --- 业务锚点 (可选，不传则自动继承) ---
        context_type: Optional[str] = None,
        context_id: Optional[str] = None,
    ):
        self.db = db
        self.op_name = operation_name
        self.explicit_user_id = user_id
        self.force_trace_id = force_trace_id
        self.attributes = attributes or BaseTraceAttributes()
        
        self.target_id = target_instance_id
        self.explicit_source_id = source_instance_id
        
        self.ctx_type = context_type
        self.ctx_id = context_id
        
        # 内部状态
        self.span_uuid = str(uuid.uuid4())
        self.trace_obj: Optional[Trace] = None
        self.start_time: float = 0.0
        self.cleanup_tokens: List[Tuple[contextvars.ContextVar, contextvars.Token]] = []
        self.is_root = False
        self.held_buffer: Optional[List[Trace]] = None

    def _set_ctx(self, var: contextvars.ContextVar, value: Any):
        token = var.set(value)
        self.cleanup_tokens.append((var, token))

    async def __aenter__(self) -> "TraceManager":
        self.start_time = time.time()

        # 1. 缓冲区初始化 (如果是根节点)
        buffer = _ctx_buffer.get()
        if buffer is None:
            self.is_root = True
            buffer = []
            self._set_ctx(_ctx_buffer, buffer)
            # 初始化 Hooks 列表 (仅 Root)
            self._set_ctx(_ctx_flush_hooks, [])
            
        # 保存引用，供 Flush 使用，即使 Context 被重置
        self.held_buffer = buffer 

        # 2. 确定 Trace ID (优先级策略)
        # A. 显式强推 (最高优先级，用于对接业务层生成的ID)
        if self.force_trace_id:
            current_trace_id = self.force_trace_id
            # 如果这与其上下文不一致，这通常意味着我们正在开启一个新的 Trace 分支或全新的请求
            # 我们更新上下文以供子节点使用
            self._set_ctx(_ctx_trace_id, current_trace_id)
        else:
            # B. 上下文继承
            current_trace_id = _ctx_trace_id.get()
            # C. 自动生成 (仅限根节点)
            if not current_trace_id:
                if not self.is_root:
                    # 防御性：非根节点必须有 TraceID，否则是逻辑错误
                    logger.warning("TraceManager: Non-root span missing trace_id. Auto-generating one, but check your logic.")
                # 防御性：如果此时还没有 trace_id，说明中间件/入口未初始化，生成临时的
                current_trace_id = str(uuid.uuid4())
                self._set_ctx(_ctx_trace_id, current_trace_id)

        # 3. 确定 User ID (优先级策略)
        final_user_id = self.explicit_user_id or _ctx_user_id.get()
        
        if not final_user_id:
            raise ValueError(
                f"TraceManager: Missing user_id for operation '{self.op_name}'. "
                "For root spans (API entry/Worker task), you MUST pass 'user_id' explicitly."
            )
        
        # 如果是显式传入的，更新上下文以供子节点使用
        if self.explicit_user_id:
            self._set_ctx(_ctx_user_id, final_user_id)

        # 4. 推导 Parent
        stack = _ctx_span_stack.get()
        parent_span_uuid = stack[-1] if stack else None
        
        # 5. 推导 User
        user_id = _ctx_user_id.get()
        # 注意：如果 user_id 为 None，Trace 将无法通过 FK 约束。
        # 上层应用必须保证在 Request Middleware 或 Worker Context 中设置了 user_id。
        
        # 6. 推导 Source / Target
        # 当前的 Source 就是上一层的 Active Target
        final_source_id = self.explicit_source_id or _ctx_active_instance_id.get()
        
        # 7. 推导 Business Context
        inherited_ctx_type, inherited_ctx_id = _ctx_business_context.get()
        final_ctx_type = self.ctx_type or inherited_ctx_type
        final_ctx_id = self.ctx_id or inherited_ctx_id

        # 8. 构建 ORM 对象 (内存态)
        self.trace_obj = Trace(
            span_uuid=self.span_uuid,
            trace_id=current_trace_id,
            parent_span_uuid=parent_span_uuid,
            user_id=user_id,
            # Topology
            source_instance_id=final_source_id,
            target_instance_id=self.target_id,
            operation_name=self.op_name,
            # Context Anchor
            context_type=final_ctx_type,
            context_id=final_ctx_id,
            # Payload (Snapshot inputs immediately)
            attributes=self.attributes.model_dump(mode='json'),
            status=TraceStatus.PENDING,
            created_at=datetime.utcnow()
        )
        
        # 9. 加入缓冲区
        buffer.append(self.trace_obj)
        
        # 10. 更新上下文 (压栈 & 设置 Active Instance)
        # Stack Push
        new_stack = stack.copy()
        new_stack.append(self.span_uuid)
        self._set_ctx(_ctx_span_stack, new_stack)
        
        # Active Instance Update (如果当前节点有 Target，它将成为子节点的 Source)
        if self.target_id:
            self._set_ctx(_ctx_active_instance_id, self.target_id)
            
        # Business Context Update (如果有新的 Context)
        if self.ctx_type:
            self._set_ctx(_ctx_business_context, (final_ctx_type, final_ctx_id))

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            end_time = time.time()
            duration = int((end_time - self.start_time) * 1000)
            
            # 1. 更新对象状态
            self.trace_obj.duration_ms = duration
            self.trace_obj.processed_at = datetime.utcnow()
            
            # 更新 attributes (主要是 Outputs 和 meta)
            # 因为 set_output 可能被调用过，我们需要重新 dump 并合并
            current_attrs = self.attributes.model_dump(mode='json')
            self.trace_obj.attributes = current_attrs

            if exc_type:
                if issubclass(exc_type, asyncio.CancelledError):
                    self.trace_obj.status = TraceStatus.CANCELLED
                    self.trace_obj.error_message = "Operation cancelled by user."
                else:
                    self.trace_obj.status = TraceStatus.FAILED
                    self.trace_obj.error_message = str(exc_val)
                # 可选：记录 StackTrace 到 meta
                # self.set_meta("stack_trace", traceback.format_exc())
            else:
                self.trace_obj.status = TraceStatus.PROCESSED

            # 2. 还原上下文 (弹栈)
            for var, token in reversed(self.cleanup_tokens):
                try:
                    var.reset(token)
                except Exception as e:
                    logger.error(f"Error resetting context var: {e}")

            # 3. 根节点负责 Flush
            if self.is_root:
                await self._flush_buffer()
        except Exception as e:
            logger.error(f"Error in TraceManager __aexit__: {e}", exc_info=True)

    async def _flush_buffer(self):
        """将缓冲区中的所有 Span 批量写入数据库"""
        if not self.held_buffer:
            return

        try:
            # 1. 执行钩子 (同步等待数据准备就绪)
            hooks = _ctx_flush_hooks.get()
            if hooks:
                # 并发执行所有钩子，效率最高
                await asyncio.gather(*[h() for h in hooks], return_exceptions=True)
            # 批量添加
            self.db.add_all(self.held_buffer)
            # 提交事务
            await self.db.flush() 
            # 注意：由外层业务控制 Commit，或者这里也可以 Commit，取决于事务策略。
            # 通常建议 Trace 和业务逻辑在同一个大事务中提交，或者 Trace 独立提交。
            # 鉴于 Trace 是辅助性的，如果业务逻辑回滚，Trace 也回滚通常是可以接受的（除非是审计要求极高）。
            # 为了性能，我们这里只 flush，让外层去 commit。
            
            logger.debug(f"[Trace] Flushed {len(self.held_buffer)} spans.")
            
        except Exception as e:
            # Trace 写入失败不应阻断业务逻辑，记录日志即可
            logger.error(f"[Trace] Failed to flush trace buffer: {e}", exc_info=True)
        finally:
            # 清理引用，帮助 GC
            self.held_buffer.clear()

    # --- Public Helpers ---

    @staticmethod
    def on_before_flush(callback: Callable[[], Awaitable[None]]):
        """注册一个在 Trace 写入数据库前执行的异步回调"""
        hooks = _ctx_flush_hooks.get()
        if hooks is not None:
            hooks.append(callback)

    def set_attributes(self, attributes: BaseTraceAttributes):
        """设置操作输入快照"""
        self.attributes = attributes

    def set_input(self, attr_input: Any):
        """设置操作输入快照"""
        self.attributes.inputs = attr_input
        
    def set_output(self, attr_output: Any):
        """设置操作输出快照"""
        self.attributes.outputs = attr_output

    def set_meta(self, key: str, value: Any):
        """设置额外的元数据"""
        setattr(self.attributes.meta, key, value)

    def set_error(self, error: str | Exception):
        """手动标记错误 (在不需要抛出异常但需要记录失败时使用)"""
        if self.trace_obj:
            self.trace_obj.status = TraceStatus.FAILED
            self.trace_obj.error_message = str(error)