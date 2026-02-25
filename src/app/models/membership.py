# app/models/membership.py

import enum
from sqlalchemy import (
    Column, Integer, String, Enum, ForeignKey, DateTime, func, Text
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.models.product import PlanTier, BillingCycle
from app.utils.id_generator import generate_uuid

class MembershipStatus(enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"      # 已过期（回退到free等级）
    CANCELED = "canceled"    # 用户主动取消或未续费，但资格可能在周期结束前仍有效
    TRIALING = "trialing"    # 试用期

class MembershipChangeType(str, enum.Enum):
    GRANT = "grant"       # 首次授予
    RENEW = "renew"       # 续订
    UPGRADE = "upgrade"     # 升级
    DOWNGRADE = "downgrade"   # 降级
    CANCEL = "cancel"     # 取消
    EXPIRE = "expire"     # 过期
    
class Membership(Base):
    """
    会员资格表 - 用户“身份合同”的唯一权威来源。
    它回答“你是谁，你的会员等级是什么，有效期到何时”的问题。
    """
    __tablename__ = 'memberships'

    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), default=generate_uuid, unique=True, index=True, nullable=False)

    # --- 核心关系 ---
    # 一个 User ID 在这里只能出现一次
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, unique=True, index=True)
    
    # [快照] 购买内容：订阅了哪个身份型产品
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)

    # --- 用户身份快照 (成功购买账户订阅计划产品或过期后成为此刻的权威事实) ---
    plan_tier = Column(Enum(PlanTier), nullable=False, comment="[权威账户级别事实] 从 Product 表的`name`冗余而来(产品类型必须是membership")
    role_id = Column(Integer, ForeignKey('roles.id'), nullable=False, comment="[权威角色事实] 从 Product 表的`granted_role_id`冗余而来")

    # --- 生命周期与状态 ---
    status = Column(Enum(MembershipStatus), nullable=False, default=MembershipStatus.ACTIVE, index=True)
    billing_cycle = Column(Enum(BillingCycle), nullable=False, comment="购买时从 Price 表快照而来的计费周期。")
    
    current_period_start = Column(DateTime, nullable=False)
    current_period_end = Column(DateTime, nullable=True, comment="周期结束时间。NULL 表示永久有效。")
    
    # 在支付网关的订阅ID，用于同步状态
    gateway_subscription_id = Column(String(255), nullable=True, unique=True)
    
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # --- 关系定义 ---
    user = relationship("User", back_populates="membership", uselist=False)
    product = relationship("Product")
    role = relationship("Role", uselist=False)

class MembershipHistory(Base):
    """
    [New] A complete, immutable audit log of all changes to a user's membership.
    """
    __tablename__ = 'membership_history'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    
    # Snapshot of the state *before* the change (or the new state for GRANT)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    plan_tier = Column(Enum(PlanTier), nullable=False)
    role_id = Column(Integer, ForeignKey('roles.id'), nullable=False)
    status = Column(Enum(MembershipStatus), nullable=False)
    billing_cycle = Column(Enum(BillingCycle), nullable=False)
    period_start = Column(DateTime, nullable=False)
    period_end = Column(DateTime, nullable=True)
    
    # Metadata about the change itself
    change_type = Column(Enum(MembershipChangeType), nullable=False)
    change_timestamp = Column(DateTime, nullable=False, server_default=func.now())
    notes = Column(Text, nullable=True) # e.g., "Upgraded from Pro to Team plan"