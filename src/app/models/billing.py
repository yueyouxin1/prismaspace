import enum
from decimal import Decimal
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class AccountStatus(str, enum.Enum):
    ACTIVE = "active"             # 正常
    DELINQUENT = "delinquent"     # 逾期/欠费
    SUSPENDED = "suspended"       # 已暂停
    CLOSED = "closed"             # 已关闭

class Currency(str, enum.Enum):
    """
    Authoritative list of supported currencies, based on ISO 4217 codes.
    Using str as a mixin allows this enum to be easily JSON serializable.
    """
    CNY = "CNY"  # Chinese Yuan
    USD = "USD"  # United States Dollar
    EUR = "EUR"  # Euro
    JPY = "JPY"  # Japanese Yen
    GBP = "GBP"  # British Pound

class TransactionType(enum.Enum):
    DEBIT = "debit"    # 借项：扣费、消费
    CREDIT = "credit"  # 贷项：充值、退款、赠送

class TransactionStatus(enum.Enum):
    PENDING = "pending"    # 待处理
    COMPLETED = "completed"  # 已完成，已影响余额
    FAILED = "failed"      # 失败
    REFUNDED = "refunded"    # 已退款

class EntitlementBalanceStatus(enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled" # 用户主动取消，但权益可能在周期结束前仍有效
    DEPLETED = "depleted"   # 额度已用尽

class ConsumptionRecordStatus(enum.Enum):
    PENDING = "pending"      # 待处理
    COMPLETED = "completed"  # 已成功记账
    FAILED = "failed"        # 处理失败

class BillingAccount(Base):
    """计费账户表 - 平台所有财务活动的唯一权威实体。"""
    __tablename__ = 'billing_accounts'
    
    # [必要]
    id = Column(Integer, primary_key=True, comment="计费账户唯一主键ID")
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="计费账户的全局唯一标识符")
    # [必要] 财务核心字段
    balance = Column(DECIMAL(18, 8), nullable=False, default=Decimal('0.0'), comment="账户余额，使用DECIMAL保证财务计算精度")
    # [关键新增] 明确该账户的本位币
    currency = Column(Enum(Currency), nullable=False, comment="ISO 4217 code of the account's base currency")
    status = Column(Enum(AccountStatus), nullable=False, default=AccountStatus.ACTIVE, index=True)
    # [增强/未来] 与支付网关集成
    customer_id = Column(String(255), nullable=True, comment="在支付网关的客户ID")
    subscription_id = Column(String(255), nullable=True, comment="在支付网关的订阅ID")
    subscription_status = Column(String(50), nullable=True, comment="订阅状态")
    
    # [审计/安全]
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # --- 关系定义 ---
    user = relationship("User", back_populates="billing_account", uselist=False)
    team = relationship("Team", back_populates="billing_account", uselist=False)
    payment_methods = relationship("PaymentMethod", back_populates="billing_account", cascade="all, delete-orphan")
    transactions = relationship("BillingTransaction", back_populates="billing_account", cascade="all, delete-orphan")
    consumptions = relationship("ConsumptionRecord", back_populates="billing_account", cascade="all, delete-orphan")

    @property
    def owner(self):
        """获取工作空间的账单所有者（用户或团队）"""
        if not self.user and not self.team:
            raise RuntimeError("Billing owner relationships not loaded. Use eager loading.")
        
        return self.user or self.team

class BillingTransaction(Base):
    """
    计费交易表 - 计费系统的“总账本”。
    记录所有影响计费账户余额的原子事件。
    """
    __tablename__ = 'billing_transactions'
    
    # [必要]
    id = Column(Integer, primary_key=True, comment="交易唯一主键ID")
    # [必要]
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="交易的全局唯一、可对外暴露的ID")

    # --- 核心财务信息 ---
    # [必要]
    billing_account_id = Column(Integer, ForeignKey('billing_accounts.id'), nullable=False, index=True, comment="关联的计费账户ID")
    # [必要]
    amount = Column(DECIMAL(18, 8), nullable=False, comment="交易金额")
    # [必要]
    type = Column(Enum(TransactionType), nullable=False, index=True, comment="交易类型 (借项/扣费, 贷项/充值)")
    # [必要]
    status = Column(Enum(TransactionStatus), nullable=False, default=TransactionStatus.COMPLETED, index=True, comment="交易状态")
    
    # --- 溯源与上下文 ---
    # [必要]
    description = Column(String(255), nullable=False, comment="对用户展示的交易描述 (e.g., 'Agent 执行用量 - 2023-10-28', 'Pro套餐月度订阅')")
    # [增强/审计]
    source_record_id = Column(Integer, ForeignKey('consumption_records.id'), nullable=True, index=True, comment="[用量] 产生此费用的源头ConsumptionRecord ID，用于审计")
    # [增强/审计]
    source_product_id = Column(Integer, ForeignKey('products.id'), nullable=True, comment="[订阅] 关联的产品ID")
    # [增强/审计]
    source_payment_id = Column(String(255), nullable=True, comment="[充值] 关联的支付网关支付ID")

    # --- 关联发票 ---
    # [增强/未来]
    # invoice_id = Column(Integer, ForeignKey('invoices.id', ondelete='SET NULL'), nullable=True, index=True, comment="此交易所属的发票ID")

    # [审计/安全]
    context = Column(JSON, nullable=True, comment="其他元数据，如退款原因、管理员操作备注等")
    # [必要]
    transaction_date = Column(DateTime, nullable=False, server_default=func.now(), index=True, comment="交易发生（被记录）的时间")
    billing_account = relationship("BillingAccount", back_populates="transactions")
    consumptions = relationship("ConsumptionRecord")
    
class EntitlementBalance(Base):
    """
    权益余额表 - 权威地记录并追踪一个已授予权益的
    总额度、消耗量和生命周期。它是一个与财务账户绑定的、可实时更新的“权益钱包”。
    """
    __tablename__ = 'entitlement_balances'
    
    id = Column(Integer, primary_key=True)
    # 权益归属
    owner_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True, index=True)
    owner_team_id = Column(Integer, ForeignKey('teams.id', ondelete='CASCADE'), nullable=True, index=True)
    # --- 溯源 (Origin) ---
    # 条款来源：我被创建时，依据的是哪条“合同条款”？
    source_entitlement_id = Column(Integer, ForeignKey('product_entitlements.id'), nullable=False)

    # 权益主体：我是关于哪个“具体计量单元”的权益？
    # 为了运行时的高效查询而做的性能冗余
    feature_id = Column(Integer, ForeignKey('features.id'), nullable=False, index=True)

    # --- 权益的量化与状态 ---
    
    #  授予那一刻的总配额快照，权威的“既定事实”
    granted_quota = Column(DECIMAL(16, 4), nullable=False) 
    
    # 已消耗的用量
    consumed_usage = Column(DECIMAL(16, 4), nullable=False, default=Decimal('0.0'))

    # 生命周期
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)
    status = Column(Enum(EntitlementBalanceStatus), default=EntitlementBalanceStatus.ACTIVE, nullable=False, index=True)
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # --- 关系定义 ---
    user_owner = relationship("User", back_populates="entitlement_balances")
    team_owner = relationship("Team", back_populates="entitlement_balances")

    # 单向关系，用于审计
    source_entitlement = relationship("ProductEntitlement")
    feature = relationship("Feature")

    __table_args__ = (
        CheckConstraint(
            '(owner_user_id IS NOT NULL AND owner_team_id IS NULL) OR '
            '(owner_user_id IS NULL AND owner_team_id IS NOT NULL)',
            name='ck_entitlement_owner_exclusive'
        ),
    )

class ConsumptionRecord(Base):
    """权威计费凭证表"""
    __tablename__ = 'consumption_records'

    id = Column(Integer, primary_key=True)
    
    # --- 核心计费信息 ---
    billing_account_id = Column(Integer, ForeignKey('billing_accounts.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    feature_id = Column(Integer, ForeignKey('features.id'), nullable=False, index=True, comment="[价值归因] 本次消耗对应的计费单元ID")
    usage = Column(DECIMAL(16, 4), nullable=False)
    cost = Column(DECIMAL(18, 8), nullable=False, default=Decimal('0.0'))
    
    # --- 状态与处理信息 ---
    status = Column(Enum(ConsumptionRecordStatus), default=ConsumptionRecordStatus.PENDING, nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    
    # --- 预留与溯源 ---
    reservation_snapshot = Column(JSON, nullable=True)
    trace_span_id = Column(Integer, ForeignKey('traces.id'), unique=True, nullable=True, index=True)
    
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    # --- 关系 (用于 Worker 加载) ---
    user = relationship("User")
    feature = relationship("Feature")
    billing_account = relationship("BillingAccount", back_populates="consumptions")
    trace = relationship("Trace", back_populates="consumption_record")

class PaymentGateway(Base):
    """支付网关权威字典表"""
    __tablename__ = 'payment_gateways'
    
    id = Column(Integer, primary_key=True)
    # 唯一的网关标识符，用于代码逻辑
    name = Column(String(50), nullable=False, unique=True, comment="e.g., 'stripe', 'alipay', 'wechat_pay'")
    # UI上显示的名称
    label = Column(String(100), nullable=False, comment="e.g., 'Stripe', '支付宝', '微信支付'")
    # 该网关是否启用
    is_active = Column(Boolean, nullable=False, default=True)

class PaymentMethod(Base):
    """
    支付方式的抽象基表，采用“连接表继承”。
    每一行代表一个属于某个计费账户的具体支付工具。
    """
    __tablename__ = 'payment_methods'
    
    id = Column(Integer, primary_key=True, comment="所有支付方式的全局唯一主键ID")
    
    # --- 核心关联 ---
    # [必要]
    billing_account_id = Column(Integer, ForeignKey('billing_accounts.id', ondelete='CASCADE'), nullable=False, index=True, comment="所属的计费账户ID")
    # [必要]
    gateway_id = Column(Integer, ForeignKey('payment_gateways.id'), nullable=False, comment="所使用的支付网关ID")
    
    # --- 通用属性 ---
    # [必要]
    is_default = Column(Boolean, nullable=False, default=False, index=True, comment="是否为该账户的默认支付方式")
    # [增强/QoL]
    label = Column(String(100), nullable=True, comment="用户为此支付方式设置的别名 (e.g., '公司招行卡')")
    # [必要]
    is_active = Column(Boolean, nullable=False, default=True, comment="该支付方式是否可用")
    
    # --- SQLAlchemy 多态配置 ---
    # [必要]
    gateway_type = Column(String(50), nullable=False, comment="多态鉴别器 (e.g., 'wechat_pay', 'alipay'),其值来自payment_gateways->name")
    
    __mapper_args__ = {
        'polymorphic_on': gateway_type
    }
    
    billing_account = relationship("BillingAccount", back_populates="payment_methods")
    gateway = relationship("PaymentGateway")

class CreditCardPaymentMethod(PaymentMethod):
    """信用卡的具体实现"""
    __tablename__ = 'payment_methods_credit_card'
    
    # [必要]
    payment_method_id = Column(Integer, ForeignKey('payment_methods.id', ondelete='CASCADE'), primary_key=True, comment="主键，同时是到父表的外键")
    
    # --- 信用卡专属的、非敏感信息 ---
    # [必要]
    gateway_customer_id = Column(String(255), nullable=False, comment="该用户在支付网关的客户ID (e.g., Stripe's cus_...)")
    # [必要]
    gateway_payment_method_id = Column(String(255), nullable=False, unique=True, comment="该卡在支付网关的唯一ID (e.g., Stripe's pm_...)")
    
    # [增强/QoL] - 用于UI展示
    brand = Column(String(50), comment="卡品牌 (e.g., 'Visa', 'Mastercard')")
    last4 = Column(String(4), comment="卡号末四位")
    exp_month = Column(Integer, comment="过期月份")
    exp_year = Column(Integer, comment="过期年份")
    
    __mapper_args__ = {
        'polymorphic_identity': 'credit_card',
    }

class AlipayPaymentMethod(PaymentMethod):
    """支付宝账户的具体实现"""
    __tablename__ = 'payment_methods_alipay'
    
    # [必要]
    payment_method_id = Column(Integer, ForeignKey('payment_methods.id', ondelete='CASCADE'), primary_key=True)
    
    # --- 支付宝专属信息 ---
    # [必要]
    gateway_customer_id = Column(String(255), nullable=False, comment="该用户在支付网关的客户ID")
    # [必要]
    gateway_payment_method_id = Column(String(255), nullable=False, unique=True, comment="该支付宝账户在支付网关的唯一ID")
    
    # [增强/QoL]
    alipay_user_id = Column(String(100), nullable=True, comment="用户的支付宝ID (脱敏后)")
    
    __mapper_args__ = {
        'polymorphic_identity': 'alipay',
    }