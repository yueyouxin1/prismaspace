# app/services/identity/user_service.py

import re
import logging
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.core.context import AppContext
from app.core.security import create_access_token, get_password_hash, verify_password
from app.constants.product_constants import PLAN_FREE
from app.constants.role_constants import ROLE_PLAN_FREE
from app.services.identity.membership_service import MembershipService 
from app.dao.identity.user_dao import UserDao
from app.dao.billing.billing_account_dao import BillingAccountDao
from app.dao.workspace.workspace_dao import WorkspaceDao
from app.dao.product.product_dao import ProductDao
from app.models.identity import User
from app.models.billing import BillingAccount, Currency
from app.models.membership import Membership, MembershipChangeType
from app.models.workspace import Workspace
from app.models.product import Product, ProductType, PlanTier, BillingCycle
from app.schemas.identity.user_schemas import UserCreate, UserRead, Token, TokenRequest
from app.services.base_service import BaseService
from app.services.exceptions import (
    ServiceException,
    EmailAlreadyExistsError,
    PhoneNumberExistsError,
    InvalidCredentialsError,
    UserNotFound,
    ConfigurationError,
)
from app.core.config import settings

# A simple regex for email validation at the service layer
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

class UserService(BaseService):
    def __init__(self, context: AppContext):
        self.context = context
        self.db = context.db
        self.user_dao = UserDao(context.db)
        self.billing_account_dao = BillingAccountDao(context.db)
        self.product_dao = ProductDao(context.db)
        self.workspace_dao = WorkspaceDao(context.db)
        self.membership_service = MembershipService(context)

    # --- Public DTO-returning "Wrapper" Method ---
    async def register_user(self, user_create: UserCreate) -> UserRead:
        new_user = await self._register_user(user_create)
        return UserRead.model_validate(new_user)

    async def login_for_access_token(self, token_request: TokenRequest) -> Token:
        user = await self._authenticate_user(token_request)
        # Create the access token using the user's UUID for better security
        # rather than the sequential primary key 'id'.
        access_token = create_access_token(subject=user.uuid)
        return Token(access_token=access_token)

    # --- Internal ORM-returning "Workhorse" Method ---
    async def _register_user(self, user_create: UserCreate) -> User:
        # --- 1. 验证 (Validation) - 保持不变 ---
        if user_create.email:
            if await self.user_dao.get_by_email(user_create.email):
                raise EmailAlreadyExistsError("Email already registered.")
        
        if user_create.phone_number:
            if await self.user_dao.get_by_phone_number(user_create.phone_number):
                raise PhoneNumberExistsError("Phone number already registered.")

        free_plan_product = await self.product_dao.get_one(
            where={"plan_tier": PlanTier.FREE, "type": ProductType.MEMBERSHIP},
            withs=["prices", "entitlements"]
        )
        if not free_plan_product or not free_plan_product.granted_role_id:
            logging.critical("FATAL: Default 'plan:free' MEMBERSHIP product not configured in the database.")
            raise ConfigurationError("System is not configured for new user registration.")
            
        # --- 2. 对象构建 (Object Construction) ---
        new_billing_account = BillingAccount(currency=Currency(settings.SITE_CURRENCY))
        hashed_password = get_password_hash(user_create.password) if user_create.password else None
        user_data_for_model = user_create.model_dump(exclude={"password"}, exclude_none=True)
        new_user = User(**user_data_for_model, password_hash=hashed_password)
        personal_workspace = Workspace(name=f"{new_user.nick_name or new_user.email}'s Workspace")

        # --- 3. 关系链接 (Relationship Linking) - 使用 ORM 的魔力 ---
        new_user.billing_account = new_billing_account
        personal_workspace.user_owner = new_user

        # --- 4. 原子持久化 (Atomic Persistence) ---
        # 将所有新世界的“亚当和夏娃”一次性提交
        try:
            # Add the user-centric objects first
            self.db.add_all([new_user, personal_workspace])
            await self.db.flush() # Flush to get IDs for the user

            # [CORRECT ORCHESTRATION]
            # Delegate the creation of membership and its associated entitlements to the expert service.
            # This is a standard business process that should not be replicated here.
            await self.membership_service.grant_membership_from_product(
                user=new_user,
                new_product=free_plan_product,
                cycle_start=datetime.utcnow(),
                cycle_end=None, # Free plans are perpetual
                change_type=MembershipChangeType.GRANT
            )
        except IntegrityError as e:
            print(f"Database integrity error during registration: {e}")
            raise ServiceException("A user with these details might already exist.")
        except Exception as e:
            # 捕获其他所有意外的数据库错误
            print(f"Unexpected database error during registration: {e}")
            raise ServiceException("Failed to create user due to a database error.")

        final_user = await self.user_dao.get_by_pk(new_user.id)
        if not final_user:
            raise ServiceException("Failed to retrieve newly created user after registration.")
            
        return final_user

    async def _authenticate_user(self, token_request: TokenRequest) -> User:
        """
        Authenticates a user based on the provided grant type and credentials.

        :param token_request: Pydantic schema with authentication data.
        :return: The authenticated User ORM object.
        :raises InvalidCredentialsError: If authentication fails for any reason.
        :raises NotImplementedError: For grant types that are not yet implemented.
        """
        if token_request.grant_type == "password":
            user = await self._authenticate_with_password(token_request.identifier, token_request.password)
        
        elif token_request.grant_type == "verification_code":
            # [占位] Future implementation for one-time code authentication
            # user = await self._authenticate_with_code(token_request.identifier, token_request.code)
            raise NotImplementedError("Verification code authentication is not yet implemented.")
        
        elif token_request.grant_type == "oauth_wechat":
            # [占位] Future implementation for WeChat OAuth
            # user = await self._authenticate_with_wechat(token_request.oauth_code)
            raise NotImplementedError("WeChat OAuth is not yet implemented.")
        
        else:
            raise InvalidCredentialsError("Unsupported grant type.")

        if not user:
            raise InvalidCredentialsError("Invalid credentials provided.")
        
        return user

    async def _authenticate_with_password(self, identifier: str, password: str) -> User | None:
        """Helper method for password-based authentication."""
        if EMAIL_REGEX.match(identifier):
            user = await self.user_dao.get_by_email(identifier)
        else:
            # Assume it's a phone number if not an email format
            user = await self.user_dao.get_by_phone_number(identifier)

        if not user:
            return None # Do not distinguish between "user not found" and "wrong password"

        if not user.password_hash or not verify_password(password, user.password_hash):
            return None

        return user