from decimal import Decimal
from typing import NamedTuple, List, Dict, Optional
from pydantic import BaseModel, field_validator, Field

class ReservationResult(NamedTuple):
    """
    A structured result of a successful reservation.
    It contains all information needed to create a comprehensive Trace record.
    """
    estimated_cost: Decimal # The TOTAL estimated cost, equal to wallet + entitlements_cost
    reserved_from_wallet: Decimal
    reserved_from_entitlements: Dict[int, Decimal] # {entitlement_id: usage_consumed}
    # [新增] 包含预估时使用的价格快照
    price_snapshot: Dict

    def to_snapshot_dict(self) -> dict:
        """ 一个辅助方法，用于生成可被 JSON 序列化的字典。"""
        return {
            "estimated_total_cost": str(self.estimated_cost),
            "reserved_from_wallet": str(self.reserved_from_wallet),
            "reserved_from_entitlements": {str(k): str(v) for k, v in self.reserved_from_entitlements.items()}
        }

class ReservationReceipt(NamedTuple):
    id: str
    result: ReservationResult

class ReservationSnapshot(BaseModel):
    reserved_from_wallet: Decimal
    estimated_total_cost: Decimal
    reserved_from_entitlements: Dict[str, Decimal]
    
    @field_validator('*', mode='before')
    @classmethod
    def convert_strings_to_decimals(cls, v):
        if isinstance(v, str):
            try:
                return Decimal(v)
            except:
                pass
        return v