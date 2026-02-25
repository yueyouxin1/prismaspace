import enum
from sqlalchemy import (
    Column, Integer, String, Text, JSON, Boolean, Enum, ForeignKey,
    DateTime, func, Index, DECIMAL, UniqueConstraint, CheckConstraint
)
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.utils.id_generator import generate_uuid

class UserType(enum.Enum):
    REGULAR = "regular"   # 普通的、用户自主注册的账户
    MANAGED = "managed"   # 由父账户/团队管理的受限账户

class UserStatus(enum.Enum): PENDING = "pending"; ACTIVE = "active"; SUSPENDED = "suspended"

class InvitationStatus(enum.Enum): PENDING = "pending"; ACCEPTED = "accepted"; EXPIRED = "expired"; CANCELED = "canceled"

class CredentialType(enum.Enum):
    INVITATION_LINK = "invitation_link" # 传统的邮件/链接邀请
    MAGIC_LINK = "magic_link"        # 用于无密码指派的一次性登录链接
    ACCESS_CODE = "access_code"        # 短期有效的加入码 (如会议邀请)

class TargetIdentifierType(enum.Enum):
    EMAIL = "email"
    PHONE = "phone"
    USER_ID = "user_id" # 直接邀请平台内已有用户

class User(Base):
    """用户表 - 平台所有身份的根本。代表一个真实的人或一个被管理的子账户。"""
    __tablename__ = 'users'
    
    # [必要] 基础身份标识
    id = Column(Integer, primary_key=True, comment="用户唯一主键ID")
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="用户的全局唯一标识符，用于API暴露")
    
    # [必要] 登录凭证 (至少需要一种)
    email = Column(String(255), unique=True, nullable=True, index=True, comment="用户邮箱，用于登录和通知，唯一")
    phone_number = Column(String(20), unique=True, nullable=True, index=True, comment="用户手机号，用于登录和通知，唯一")
    password_hash = Column(String(255), nullable=True, comment="哈希后的用户密码，允许为空以支持无密码/待激活状态")
    
    # [增强/QoL] 用户资料
    nick_name = Column(String(100), nullable=True, comment="用户昵称")
    avatar = Column(String(512), nullable=True, comment="用户头像URL")
    
    # [必要] 账户类型与生命周期
    status = Column(Enum(UserStatus), nullable=False, default=UserStatus.PENDING, comment="用户账户状态 (待激活, 活跃, 禁用)")
    user_type = Column(Enum(UserType), nullable=False, default=UserType.REGULAR, comment="用户账户类型 (普通用户, 被管理子账户)")
    
    # [增强/协作] 用于实现“指派制”子账号
    managing_entity_type = Column(String(50), nullable=True, comment="当user_type为MANAGED时，管理该账户的实体类型 (e.g., 'team')")
    managing_entity_id = Column(Integer, nullable=True, index=True, comment="当user_type为MANAGED时，管理该账户的实体ID")

    # [企业级/未来] 用于SSO/SCIM集成
    external_idp = Column(String(50), nullable=True, comment="身份提供商标识, e.g., 'okta', 'azure_ad'")
    external_id = Column(String(255), nullable=True, comment="在外部IdP中的唯一ID")
    
    # [审计/安全] 时间戳
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="账户创建时间")
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="账户信息最后更新时间")
    last_login_at = Column(DateTime, nullable=True, comment="用户最后登录时间")
    
    # [必要] 计费关联
    billing_account_id = Column(Integer, ForeignKey('billing_accounts.id'), nullable=False, unique=True, index=True, comment="关联的个人计费账户ID")

    # 双向关系：指向 BillingAccount
    billing_account = relationship("BillingAccount", back_populates="user", uselist=False, cascade="all, delete-orphan", single_parent=True, lazy="joined")
    # User -> Membership 是一对一关系
    membership = relationship("Membership", back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="joined")
    # 双向关系：作为 Owner 拥有的团队
    owned_teams = relationship("Team", back_populates="owner")
    
    # 双向关系：作为成员加入的团队关联
    team_associations = relationship("TeamMember", back_populates="user", cascade="all, delete-orphan")
    entitlement_balances = relationship("EntitlementBalance", back_populates="user_owner", cascade="all, delete-orphan")
    # 指向 ApiKey 和 ActivityLog
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="user", foreign_keys="ActivityLog.actor_user_id", cascade="all, delete-orphan")

# 团队所有者必须至少拥有`plan:team`会员订阅级别，才可以使用团队功能
class Team(Base):
    """团队表 - 多个用户协作的实体，也是团队计费的主体。"""
    __tablename__ = 'teams'
    
    # [必要] 团队身份标识
    id = Column(Integer, primary_key=True, comment="团队唯一主键ID")
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="团队的全局唯一标识符")
    
    # [必要] 团队资料
    name = Column(String(255), nullable=False, comment="团队名称")
    avatar = Column(String(512), nullable=True, comment="团队头像URL")
    
    # [必要] 团队所有权与计费
    owner_id = Column(Integer, ForeignKey('users.id'), nullable=False, comment="团队所有者的用户ID，最终财务责任人")
    billing_account_id = Column(Integer, ForeignKey('billing_accounts.id'), nullable=False, unique=True, comment="关联的团队计费账户ID")
    
    # [审计/安全] 时间戳
    created_at = Column(DateTime, nullable=False, server_default=func.now(), comment="团队创建时间")
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now(), comment="团队信息最后更新时间")
    
    # 双向关系：指向团队所有者 User
    owner = relationship("User", back_populates="owned_teams")
    
    # 双向关系：指向 BillingAccount
    billing_account = relationship("BillingAccount", back_populates="team", uselist=False, cascade="all, delete-orphan", single_parent=True, lazy="joined")
    
    # 双向关系：团队中的成员列表
    members = relationship("TeamMember", back_populates="team", cascade="all, delete-orphan")
    entitlement_balances = relationship("EntitlementBalance", back_populates="team_owner", cascade="all, delete-orphan")
    # 指向 Workspace
    workspaces = relationship("Workspace", back_populates="team", cascade="all, delete-orphan")

    api_keys = relationship("ApiKey", back_populates="team")

    activity_logs = relationship("ActivityLog", back_populates="team", cascade="all, delete-orphan", order_by="desc(ActivityLog.timestamp)")

class TeamMember(Base):
    """团队成员表 - 定义用户在团队中的角色。"""
    __tablename__ = 'team_members'
    # [必要]
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid, comment="团队成员的全局唯一标识符")
    team_id = Column(Integer, ForeignKey('teams.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    # [必要] 使用外键关联到强大的RBAC角色表
    role_id = Column(Integer, ForeignKey('roles.id'), nullable=False, comment="成员在该团队的角色ID")
    # [审计/安全]
    origin_invitation_id = Column(Integer, ForeignKey('invitations.id', ondelete='SET NULL'), nullable=True, unique=True, comment="创建此成员关系的邀请ID，用于溯源")
    joined_at = Column(DateTime, nullable=False, server_default=func.now(), comment="成员加入时间")
    
    # 双向关系：指向所属团队
    team = relationship("Team", back_populates="members")
    # 双向关系：指向关联用户
    user = relationship("User", back_populates="team_associations")
    role = relationship("Role", back_populates="team_members")
    __table_args__ = (UniqueConstraint('team_id', 'user_id', name='uq_team_user'),)

class Invitation(Base):
    """
    凭证分发/邀请表 - 管理所有将用户引入协作实体的凭证。
    """
    __tablename__ = 'invitations'
    
    id = Column(Integer, primary_key=True)
    uuid = Column(String(36), nullable=False, unique=True, index=True, default=generate_uuid)
    
    # --- 凭证定义 ---
    credential_type = Column(Enum(CredentialType), nullable=False, default=CredentialType.INVITATION_LINK, comment="凭证的类型")
    token = Column(String(128), nullable=False, unique=True, index=True, comment="凭证的唯一令牌 (链接、代码等)")
    
    # --- 目标定义 ---
    # 邀请的目标可以是平台内已知的用户，也可以是外部的一个身份标识
    target_identifier_type = Column(Enum(TargetIdentifierType), nullable=True, comment="目标身份标识的类型")
    target_identifier = Column(String(255), nullable=True, index=True, comment="目标身份标识 (邮箱、手机号或平台用户ID)")

    # --- 上下文 ---
    target_entity_type = Column(String(50), nullable=False, comment="被邀请加入的实体类型 (e.g., 'team')")
    target_entity_id = Column(Integer, nullable=False, index=True, comment="被邀请加入的实体ID")
    inviter_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    role_id = Column(Integer, ForeignKey('roles.id'), nullable=False)
    
    # --- 生命周期与溯源 ---
    status = Column(Enum(InvitationStatus), nullable=False, default=InvitationStatus.PENDING)
    expires_at = Column(DateTime, nullable=False, comment="凭证过期时间")
    
    accepted_by_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_member_id = Column(Integer, nullable=True) # 指向 team_members.id

    created_at = Column(DateTime, nullable=False, server_default=func.now())
    accepted_at = Column(DateTime, nullable=True)
    
    # ... relationships ...

class ApiKey(Base):
    __tablename__ = 'api_keys'
    id = Column(Integer, primary_key=True)
    key_prefix = Column(String(8), nullable=False, unique=True, comment="可安全显示的前缀, e.g., 'sk-...'")
    key_hash = Column(String(255), nullable=False, unique=True, comment="哈希后的完整密钥")
    
    # [关键] 密钥的所有者可以是用户或工作空间
    owner_user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=True)
    owner_team_id = Column(Integer, ForeignKey('teams.id', ondelete='CASCADE'), nullable=True)
    
    description = Column(String(255), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    # [审计/安全]
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    # [必要] 计费账户的归属者 (用户或团队)
    user = relationship("User", back_populates="api_keys")
    team = relationship("Team", back_populates="api_keys")
    __table_args__ = (
        CheckConstraint(
            '(owner_user_id IS NOT NULL AND owner_team_id IS NULL) OR '
            '(owner_user_id IS NULL AND owner_team_id IS NOT NULL)',
            name='ck_apikey_owner_exclusive'
        ),
    )