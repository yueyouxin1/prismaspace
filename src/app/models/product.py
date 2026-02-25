import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from .billing import Currency

class ProductType(str, enum.Enum):
    """权威的产品类型定义，决定了产品的核心商业逻辑。"""
    MEMBERSHIP = "membership"    # 会员资格型
    ADD_ON = "add_on"            # 附加订阅型
    ONE_TIME = "one_time"        # 一次性购买型
    USAGE = "usage"              # 按量使用型（内部结算，非公开售卖）

class FeatureType(enum.Enum):
    QUOTA = "quota"      # 固定配额型功能（如 team_member_limit）
    METERED = "metered"  # 可计量型功能（如 tokens, api_calls）

class PlanTier(enum.Enum):
    FREE = "plan:free"
    PRO = "plan:pro"
    TEAM = "plan:team"
    ENTERPRISE = "plan:enterprise"

class BillingCycle(enum.Enum):
    """
    权威的计费周期枚举。
    """
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"

class Feature(Base):
    """
    功能/权益表 - 定义了平台上所有可被计量和赋予价值的原子单元。
    这是我们定价体系的“乐高积木”。
    """
    __tablename__ = 'features'
    
    id = Column(Integer, primary_key=True)
    # [必要]
    name = Column(String(255), unique=True, nullable=False, comment="功能的唯一标识符 (e.g., 'llm:tokens:input:openai:gpt-4o-2024-05-13', 'storage:5gb_month')")
    # [增强/QoL]
    label = Column(String(255), nullable=False, comment="UI上显示的名称 (e.g., 'GPT-4o 输入Token', '每月存储空间(GB)')")
    # [必要]
    type = Column(Enum(FeatureType), nullable=False, comment="功能类型")
    # 为了灵活性，不应该在数据库层使用枚举，权威来自应用层的枚举类
    feature_role = Column(String(50), nullable=True, comment="定义Feature在不同场景中扮演的角色")
    # 软删除
    is_active = Column(Boolean, nullable=False, default=True, comment="该单元当前是否可用")
    # [关键] 一个计费单元，是某个具体服务模块版本的产物
    # 可以为空，支持非技术性权益
    service_module_version_id = Column(Integer, ForeignKey('service_module_versions.id', ondelete='SET NULL'), nullable=True, index=True)
    permission_id = Column(Integer, ForeignKey('action_permissions.id', ondelete='SET NULL'), nullable=True, unique=True)

    service_module_version = relationship("ServiceModuleVersion", back_populates="features")
    permission = relationship("ActionPermission", uselist=False)

    product = relationship("Product", back_populates="feature", uselist=False, lazy="joined")

    __table_args__ = (
        # 确保对于同一个 ServiceModuleVersion，每个角色只能出现一次
        UniqueConstraint('service_module_version_id', 'feature_role', name='uq_smv_feature_role'),
    )

class Product(Base):
    """
    产品目录表 - 定义了平台上所有“可售卖”的东西。
    它可以是一个订阅套餐，也可以是一种按量计费的服务。
    """
    __tablename__ = 'products'
    
    id = Column(Integer, primary_key=True)
    # [必要]
    name = Column(String(255), nullable=False, unique=True, comment="产品的唯一标识符 (e.g., 'plan:pro', 'feature:llm:tokens:input:openai:gpt-4o-2024-05-13')")
    # [必要]
    label = Column(String(255), nullable=False, comment="UI上显示的产品名称 (e.g., 'Pro版订阅 (月付)', 'gpt-4o-2024-05-13 输入Token用量')")
    # [增强/QoL]
    description = Column(Text, nullable=True)
    type = Column(Enum(ProductType), nullable=False, comment="产品类型")
    # 这个字段只对 type = 'membership' 的产品有意义，所以设为 nullable=True
    plan_tier = Column(Enum(PlanTier), nullable=True, comment="如果产品是会员资格型，此字段记录其对应的会员等级")
    # [不可公开] 这是一个到系统角色（team_id is NULL）的内部指针。
    granted_role_id = Column(Integer, ForeignKey('roles.id'), nullable=True, comment="该产品授予用户的系统角色ID")
    # 如果这是一个 USAGE 类型的 Product，它必须且只能代表一个 Feature。
    feature_id = Column(Integer, ForeignKey('features.id'), nullable=True, unique=True)
    # [关键定义 1] 系统可用性开关
    # True 表示计费引擎可以使用它。False 表示它已在系统中作废。
    is_active = Column(Boolean, nullable=False, default=True, comment="该产品当前是否可售卖")
    # [关键定义 2] 前台可见性/可购买性开关
    # True 表示这个产品会出现在网站的“定价”页面，供用户直接购买。
    is_purchasable = Column(Boolean, nullable=False, default=False, comment="该产品当前是否可公开购买")

    granted_role = relationship("Role", uselist=False)
    feature = relationship("Feature", back_populates="product", uselist=False)
    entitlements = relationship("ProductEntitlement", back_populates="product", cascade="all, delete-orphan")
    prices = relationship("Price", back_populates="product", cascade="all, delete-orphan", lazy="joined")

    __table_args__ = (
        CheckConstraint(
            (type != ProductType.MEMBERSHIP) | (plan_tier != None),
            name='ck_product_membership_requires_plan_tier'
        ),
        CheckConstraint(
            # 规则1: 如果类型是 USAGE, feature_id 必须有值。
            ( (type == ProductType.USAGE) & (feature_id != None) ) |
            # 规则2: 如果类型不是 USAGE, feature_id 必须为 NULL。
            ( type.in_([ProductType.MEMBERSHIP, ProductType.ADD_ON, ProductType.ONE_TIME]) & (feature_id == None) ),
            name='ck_product_feature_link_by_type'
        ),
    )
    
class ProductEntitlement(Base):
    """
    产品权益模板表 (静态、只读权威)
    """
    __tablename__ = 'product_entitlements'
    
    id = Column(Integer, primary_key=True)
    # [必要]
    product_id = Column(Integer, ForeignKey('products.id', ondelete='CASCADE'), nullable=False, index=True)
    # [必要]
    feature_id = Column(Integer, ForeignKey('features.id', ondelete='CASCADE'), nullable=False, index=True)
    # [必要]
    quota = Column(Integer, nullable=False, default=0, comment="包含的免费额度 (e.g., 1,000,000 for tokens, 10 for GB)")
    validity_period_days = Column(Integer, nullable=True, comment="权益的有效期（天）。NULL表示与订阅周期同步或永不过期。")
    is_resettable = Column(Boolean, nullable=False, default=False, comment="该权益是否在每个计费周期重置。")
    is_active = Column(Boolean, nullable=False, default=True, comment="该权益模板当前是否可用")
    product = relationship("Product", back_populates="entitlements")
    feature = relationship("Feature")

    __table_args__ = (UniqueConstraint('product_id', 'feature_id', name='uq_product_feature_entitlement'),)

class Price(Base):
    """
    价格表 - 为一个“产品”定义具体的价格。
    一个产品可以有多个价格（如不同货币、不同计费周期）。
    """
    __tablename__ = 'prices'
    
    id = Column(Integer, primary_key=True)
    # [关键] 价格的归属：要么属于一个产品（订阅费），要么属于一个功能（按量单价）
    product_id = Column(Integer, ForeignKey('products.id', ondelete='CASCADE'), nullable=True)
    
    # 采用统一的、能容纳宏观和微观价格的最高精度标准
    amount = Column(DECIMAL(18, 8), nullable=False, comment="价格") 
    # [必要]
    currency = Column(Enum(Currency), nullable=False, default='usd', comment="货币单位 (e.g., 'usd', 'cny')")
    
    # [增强/订阅]
    billing_cycle = Column(Enum(BillingCycle), nullable=True, comment="对于订阅，计费周期。NULL表示为一次性购买。")
    
    # 计价单位 (The Unit)
    # 例如: 'token', 'call', 'second', 'hour'
    unit = Column(String(50), nullable=True)

    # 多少个单位对应上述 amount
    # 默认是 1。但对于 token，可以设为 1000，amount 设为 $0.0015，表示 0.0015美元/1k tokens
    unit_count = Column(Integer, nullable=False, default=1)
    
    # [必要]
    is_active = Column(Boolean, nullable=False, default=True, comment="该价格当前是否有效")

    # [关键新增] 价格可以包含多个层级的计费规则
    tiers = relationship("PriceTier", back_populates="price", lazy="joined", cascade="all, delete-orphan")
    product = relationship("Product", back_populates="prices")

class PriceTier(Base):
    """
    价格层级表 - 定义了一个功能在不同用量区间的具体价格。
    这是实现阶梯式定价和按属性定价的核心。
    """
    __tablename__ = 'price_tiers'
    
    id = Column(Integer, primary_key=True)
    # [必要]
    price_id = Column(Integer, ForeignKey('prices.id', ondelete='CASCADE'), nullable=False)
    # [必要]
    feature_id = Column(Integer, ForeignKey('features.id', ondelete='CASCADE'), nullable=False)
    
    # --- 定价规则 ---
    # [必要]
    amount = Column(DECIMAL(18, 8), nullable=False, comment="价格")
    # [增强/阶梯]
    up_to = Column(Integer, nullable=True, comment="阶梯定价的上限 (e.g., 1000000), NULL表示无限")
    
    price = relationship("Price", back_populates="tiers")
    feature = relationship("Feature")