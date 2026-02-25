# src/app/schemas/product/product_schemas.py

from pydantic import BaseModel, Field, ConfigDict, model_validator, condecimal
from typing import Optional, List, Any
from app.models import ProductType, FeatureType, PlanTier, Currency, BillingCycle

# ====================================================================
# Feature Schemas (Foundation)
# ====================================================================

class FeatureCreate(BaseModel):
    name: str = Field(..., description="唯一的、机器可读的名称 (e.g., 'llm:input_tokens:openai:gpt-4o')")
    label: str = Field(..., description="UI上显示的友好名称")
    type: FeatureType = Field(..., description="Feature的类型 (quota 或 metered)")
    feature_role: Optional[str] = Field(None, description="Feature角色")
    service_module_version_name: Optional[str] = Field(None, description="关联的服务模块版本名称 (e.g., 'gpt-4o-2024-05-13')")

class FeatureRead(BaseModel):
    name: str
    label: str
    type: FeatureType
    service_module_version_name: Optional[str] = Field(None, alias="service_module_version.name")

    model_config = ConfigDict(from_attributes=True, extra='ignore')

# ====================================================================
# Price Schemas
# ====================================================================

class TierInfo(BaseModel):
    up_to: Optional[int] = Field(None, description="阶梯定价的上限 (e.g., 1000000), NULL表示无限")
    # [IMPROVEMENT] Use condecimal for precision
    amount: condecimal(max_digits=18, decimal_places=8) = Field(..., description="此阶梯的定价")
    model_config = ConfigDict(from_attributes=True)
    
class PriceInfo(BaseModel):
    """
    用于 PricingProvider 内部和缓存的标准化价格信息 DTO。
    """
    currency: Currency = Field(..., description="ISO 4217 currency code")
    unit: str = Field(..., description="计价单位 (e.g., 'token', 'call')")
    unit_count: int = Field(1, description="多少个'unit'对应上述价格。例如，unit='token', unit_count=1000表示每千token的价格。")
    
    # [CRITICAL CHANGE] 支持分层或平面价格
    amount: Optional[condecimal(max_digits=18, decimal_places=8)] = Field(None, description="平面价格的金额")
    tiers: List[TierInfo] = Field(default_factory=list, description="阶梯定价规则")

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode='after')
    def check_price_type(self):
        if self.amount is None and not self.tiers:
            raise ValueError("PriceInfo must have either 'amount' or 'tiers'.")
        if self.amount is not None and self.tiers:
            raise ValueError("PriceInfo cannot have both 'amount' and 'tiers'.")
        return self

class PriceCreate(BaseModel):
    amount: condecimal(max_digits=18, decimal_places=8) = Field(..., description="价格金额")
    currency: Currency = Field(..., description="ISO 4217 currency code")

    # For Subscription types (MEMBERSHIP, ADD_ON)
    billing_cycle: Optional[BillingCycle] = Field(None, description="计费周期。对于一次性购买，此值为null。")

    # For Usage types
    unit: Optional[str] = Field(None, description="计价单位 (e.g., 'token', 'call')。仅用于按量计费产品。")
    unit_count: int = Field(1, description="多少个'unit'对应一个'amount'价格。例如，unit='token', unit_count=1000 表示每千token的价格。")

    # 未来可扩展阶梯定价
    # tiers: List[PriceTierCreate] = []

    @model_validator(mode='before')
    @classmethod
    def check_price_logic(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if data.get('unit') and data.get('billing_cycle'):
                raise ValueError("A usage-based price (with 'unit') cannot have a 'billing_cycle'.")
        return data

class PriceRead(PriceCreate):
    id: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


# ====================================================================
# ProductEntitlement Schemas
# ====================================================================

class ProductEntitlementCreate(BaseModel):
    feature_name: str = Field(..., description="关联的Feature的唯一名称")
    quota: int = Field(..., description="包含的配额")
    is_resettable: bool = Field(False, description="此权益是否在每个计费周期重置")

class ProductEntitlementRead(BaseModel):
    id: int
    quota: int
    is_resettable: bool
    feature: FeatureRead # [关键] 嵌套完整的Feature信息，方便前端展示
    
    model_config = ConfigDict(from_attributes=True)


# ====================================================================
# Product Schemas (Top-Level Aggregation)
# ====================================================================

class ProductBase(BaseModel):
    name: str = Field(..., description="产品的唯一标识符 (e.g., 'plan:pro', 'addon:storage:10gb')")
    label: str = Field(..., description="UI上显示的产品名称")
    description: Optional[str] = None
    type: ProductType

class ProductCreate(ProductBase):
    """
    用于Manager层的创建模型，包含了一些方便转换的字段。
    """
    # For MEMBERSHIP type
    plan_tier: Optional[PlanTier] = None
    granted_role_name: Optional[str] = Field(None, description="购买此会员产品后授予的系统角色名称 (e.g., 'plan:pro')")

    # For USAGE type
    feature_name: Optional[str] = Field(None, description="对于USAGE类型的产品，必须关联一个Feature")

    @model_validator(mode='before')
    @classmethod
    def check_type_specific_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            prod_type = data.get('type')
            if prod_type == ProductType.MEMBERSHIP:
                if not data.get('plan_tier') or not data.get('granted_role_name'):
                    raise ValueError("MEMBERSHIP products require 'plan_tier' and 'granted_role_name'.")
            elif prod_type == ProductType.USAGE:
                if not data.get('feature_name'):
                    raise ValueError("USAGE products require a 'feature_name'.")
        return data

class ProductCreateFull(ProductCreate):
    """
    用于`POST /products` API的聚合模型，方便一次性创建完整产品。
    """
    prices: List[PriceCreate] = Field(default_factory=list)
    entitlements: List[ProductEntitlementCreate] = Field(default_factory=list)

class ProductUpdate(BaseModel):
    """[New] Schema for updating a product's non-critical metadata."""
    label: Optional[str] = None
    description: Optional[str] = None
    # 'is_active' can be another updatable field, managed by a separate 'archive' endpoint.
    
class ProductReadFull(ProductBase):
    """
    用于API响应的完整、聚合的视图模型。对前端非常友好。
    """
    id: int
    is_active: bool
    is_purchasable: bool
    plan_tier: Optional[PlanTier] = None
    prices: List[PriceRead] = []
    entitlements: List[ProductEntitlementRead] = []
    
    model_config = ConfigDict(from_attributes=True)