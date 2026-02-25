# src/app/schemas/billing/entitlement_schemas.py

from pydantic import BaseModel, Field, ConfigDict, computed_field, model_validator
from typing import Optional, Any
from datetime import datetime
from decimal import Decimal
from app.models import ProductType
from ..product.product_schemas import FeatureRead

class EntitlementBalanceRead(BaseModel):
    id: int
    granted_quota: Decimal
    consumed_usage: Decimal
    status: str
    start_date: datetime
    end_date: Optional[datetime]
    
    feature: FeatureRead

    # [Hardened] Add source product info for better UI grouping
    source_product_name: str
    source_product_type: ProductType

    @computed_field
    @property
    def remaining_quota(self) -> Decimal:
        return self.granted_quota - self.consumed_usage

    @model_validator(mode='before')
    @classmethod
    def pre_process_orm_obj(cls, data: Any) -> Any:
        # 显式提取每个需要的字段
        return {
            'id': data.id,
            'granted_quota': data.granted_quota,
            'consumed_usage': data.consumed_usage,
            'status': data.status,
            'start_date': data.start_date,
            'end_date': data.end_date,
            'feature': data.source_entitlement.feature,
            'source_product_name': data.source_entitlement.product.name,
            'source_product_type': data.source_entitlement.product.type
        }
        
        return data

    model_config = ConfigDict(from_attributes=True, extra="ignore")