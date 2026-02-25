# src/app/services/billing/context.py

import uuid
import logging
import asyncio
from decimal import Decimal
from typing import List, Optional, NamedTuple, Dict, Tuple, Set
from app.core.context import AppContext
from app.models import User, Team, Feature, ConsumptionRecord, ConsumptionRecordStatus
from .interceptor import BillingInterceptor
from .types.interceptor import ReservationResult, ReservationReceipt
from .reconciliation_service import ReconciliationService
from app.services.exceptions import ServiceException

class BillingContext:
    """
    [Hardened] 管理计费事件生命周期的事务协调器。
    它使用“收据模型”来确保所有预留都被正确处理（结算或取消）。
    
    改进点：增强了在异常退出时的容错能力，确保资金安全。
    """
    def __init__(self, context: AppContext, billing_entity: User | Team):
        self.app_context = context
        self.actor = context.actor
        if not billing_entity:
            raise ServiceException("BillingContext has no valid billing owner.")
        self.billing_entity = billing_entity
        
        self._interceptor = BillingInterceptor(context, billing_entity=billing_entity)
        self._reconciliation_service = ReconciliationService(context)
        
        # 跟踪所有已签发的收据: {receipt_id: (ReservationResult, Optional[ConsumptionRecord])}
        # 第二个元素如果是 None，表示尚未报告用量
        self._reservations_made: Dict[str, Tuple[ReservationResult, Optional[ConsumptionRecord]]] = {}
        
        # 跟踪所有已核销（即调用了 report_usage）的收据ID
        self._reported_receipt_ids: Set[str] = set()
        
        self._settled = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """
        退出上下文时的清理逻辑。
        这是一个“尽力而为”的操作，优先保证资金安全（冲正），其次保证记账准确（结算）。
        """
        if self._settled:
            return False

        # 记录上下文是否因异常而退出
        has_error = exc_type is not None
        if has_error:
            logging.warning(
                f"[BillingContext] Exiting with exception ({exc_type.__name__}). "
                "Starting defensive cancellation and settlement."
            )

        try:
            # 1. [优先] 冲正所有未报告的预留 (Refund Logic)
            # 即使业务逻辑抛出异常，我们也必须退还那些只预留但未消耗的资金
            await self._cancel_unreported_reservations()
        except Exception as e:
            logging.critical(f"[BillingContext] CRITICAL: Failed during cancellation phase: {e}", exc_info=True)
            # 注意：这里即使取消失败，我们也要继续尝试结算已报告的用量，不能return

        try:
            # 2. [次要] 结算已报告的用量 (Settlement Logic)
            # 即使业务逻辑抛出异常，只要 report_usage 成功被调用过，说明资源已被消耗（如LLM已调用），
            # 那么这部分钱就应该被扣除。
            await self._settle_reported_usages()
        except Exception as e:
            logging.critical(f"[BillingContext] CRITICAL: Failed during settlement phase: {e}", exc_info=True)

        self._settled = True
        
        # 返回 False 确保原始业务异常（如果有）会继续向上传播，不会被吞没
        return False

    async def reserve(self, feature: Feature, reserve_usage: Decimal) -> ReservationReceipt:
        if self._settled:
            logging.warning("Attempted to reserve after billing context was settled. Returning empty receipt.")
            return ReservationReceipt(id="", result=ReservationResult(Decimal(0), Decimal(0), {}, {}))

        # 执行预留 (Lua Script)
        result = await self._interceptor.reserve(feature, reserve_usage, self.billing_entity.billing_account.currency)
        
        receipt_id = str(uuid.uuid4())
        # 记录预留结果，此时 ConsumptionRecord 为 None
        self._reservations_made[receipt_id] = (result, None)
        
        return ReservationReceipt(id=receipt_id, result=result)

    async def report_usage(
        self,
        receipt: ReservationReceipt,
        feature: Feature,
        actual_usage: Decimal,
        trace_span_id: Optional[int] = None
    ):
        """
        用户确认资源已被消耗。
        """
        if self._settled:
            logging.warning("Usage reported after billing context has been settled. Ignoring.")
            return

        if not receipt or not receipt.id:
            return

        if receipt.id not in self._reservations_made:
            # 这通常意味着代码逻辑错误，使用了无效的收据
            raise ServiceException("Invalid or already processed reservation receipt provided to report_usage.")

        if receipt.id in self._reported_receipt_ids:
            # 防止重复报告
            return

        # 1. 构建待结算记录
        new_record = ConsumptionRecord(
            user_id=self.actor.id,
            billing_account_id=self.billing_entity.billing_account.id,
            feature_id=feature.id,
            usage=actual_usage,
            reservation_snapshot=receipt.result.to_snapshot_dict(),
            trace_span_id=trace_span_id,
            status=ConsumptionRecordStatus.PENDING,
        )

        # 2. 更新内部状态
        # 注意顺序：先更新数据结构，再标记ID，保证一致性
        reservation_result, _ = self._reservations_made[receipt.id]
        self._reservations_made[receipt.id] = (reservation_result, new_record)
        self._reported_receipt_ids.add(receipt.id)

    async def _cancel_unreported_reservations(self):
        """
        [Robust] 并发取消所有未报告的预留，确保 return_exceptions=True。
        """
        # 找出所有在 _reservations_made 中但不在 _reported_receipt_ids 中的ID
        all_ids = set(self._reservations_made.keys())
        unreported_ids = all_ids - self._reported_receipt_ids
        
        if not unreported_ids:
            return

        logging.info(f"[BillingContext] Cancelling {len(unreported_ids)} unreported reservations...")
        
        cancellation_tasks = []
        for receipt_id in unreported_ids:
            reservation_data, _ = self._reservations_made[receipt_id]
            # 创建协程任务
            task = self._reconciliation_service.cancel_reservation(self.billing_entity, reservation_data)
            cancellation_tasks.append(task)
        
        if cancellation_tasks:
            # [关键修复] return_exceptions=True 确保一个任务失败不会导致其他任务被取消或抛出异常
            results = await asyncio.gather(*cancellation_tasks, return_exceptions=True)
            
            # 检查是否有异常发生并记录
            for idx, res in enumerate(results):
                if isinstance(res, Exception):
                    logging.error(
                        f"[BillingContext] Failed to cancel reservation (idx={idx}). "
                        f"Shadow ledger may be drifted. Error: {res}"
                    )

    async def _settle_reported_usages(self):
        """
        [Robust] 批量结算已报告的用量。
        """
        if not self._reported_receipt_ids:
            return

        consumption_queue = []
        for receipt_id in self._reported_receipt_ids:
            _, record = self._reservations_made[receipt_id]
            if record:
                consumption_queue.append(record)

        if not consumption_queue:
            return

        try:
            # 1. 批量写入数据库
            self.app_context.db.add_all(consumption_queue)
            await self.app_context.db.flush()

            # 2. 将任务推送到后台 Worker
            # 使用 gather 提高并发性能，同样使用 return_exceptions 保护
            enqueue_tasks = []
            for record in consumption_queue:
                if record.id is None:
                    continue
                
                task = self.app_context.arq_pool.enqueue_job(
                    'process_consumption_task', 
                    record.id, 
                    self.actor.uuid
                )
                enqueue_tasks.append(task)
            
            if enqueue_tasks:
                results = await asyncio.gather(*enqueue_tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, Exception):
                        logging.error(f"[BillingContext] Failed to enqueue consumption task: {res}")

        except Exception as e:
            logging.error(f"[BillingContext] Failed to persist consumption records: {e}")
            # 这是一个严重错误，意味着我们虽然扣了用户的钱（逻辑上），但没能记账。
            # 此时数据库事务可能会回滚（取决于外层），如果回滚，则记录丢失，但 Redis 影子账本已扣除。
            # 这会导致影子账本“少钱”。ReconciliationService 的 periodic sync 会最终修复这个问题。
            raise e