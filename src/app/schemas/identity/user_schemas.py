# app/schemas/identity/user_schemas.py

from pydantic import (
    BaseModel, 
    EmailStr, 
    Field, 
    field_validator, 
    model_validator, 
    ConfigDict
)
from typing import Optional, Literal, Any

# ==============================================================================
# 1. Shared Properties (基础模型)
#    - 包含所有用户模型共有的字段。
# ==============================================================================

class UserBase(BaseModel):
    """
    用户模型的基础字段，用于被其他模型继承。
    """
    email: Optional[EmailStr] = Field(None, description="用户邮箱")
    phone_number: Optional[str] = Field(None, description="用户手机号")
    nick_name: Optional[str] = Field(None, max_length=100, description="用户昵称")
    avatar: Optional[str] = Field(None, max_length=512, description="用户头像URL")


# ==============================================================================
# 2. Input Schemas (API输入验证模型)
# ==============================================================================

class UserCreate(UserBase):
    """
    用于用户注册的Schema。
    使用 @model_validator 来处理跨字段的复杂验证逻辑。
    """
    password: Optional[str] = Field(None, min_length=8, description="用户密码，对于验证码注册可为空")

    @field_validator('phone_number')
    @classmethod
    def validate_phone_number_format(cls, v: Optional[str]) -> Optional[str]:
        """
        [占位] 对手机号码格式进行验证。
        未来此处应添加更严格的验证，例如使用 'phonenumbers' 库。
        """
        if v is None:
            return v
        # Simple placeholder validation
        if not v.startswith('+') or not v[1:].isdigit() or len(v) < 10:
             raise ValueError('Invalid phone number format. Must include country code, e.g., +8613800138000')
        return v
    
    @model_validator(mode='before')
    @classmethod
    def check_registration_logic(cls, data: Any) -> Any:
        """
        模型级别的验证器，在字段验证之前运行 ('before')。
        它确保了注册逻辑的完整性：
        1. 必须提供邮箱或手机号中的至少一个。
        2. 如果提供了邮箱，则密码是必需的。
        """
        if isinstance(data, dict):
            email = data.get('email')
            phone = data.get('phone_number')
            password = data.get('password')

            if not email and not phone:
                raise ValueError('Either email or phone_number must be provided for registration.')

            if email and not password:
                raise ValueError('Password is required for email-based registration.')
        return data


class TokenRequest(BaseModel):
    """
    统一登录/令牌请求的Schema，支持多种授权类型。
    """
    grant_type: Literal["password", "verification_code", "oauth_wechat"]
    
    # --- 凭证字段 ---
    identifier: Optional[str] = Field(None, description="登录标识符，可以是邮箱或手机号")
    password: Optional[str] = Field(None, description="密码")
    code: Optional[str] = Field(None, description="短信或邮件验证码")
    oauth_code: Optional[str] = Field(None, description="第三方OAuth提供商返回的授权码 (未来使用)")

    @model_validator(mode='before')
    @classmethod
    def check_credentials_for_grant_type(cls, data: Any) -> Any:
        """
        根据 grant_type 验证是否提供了必要的凭证字段。
        """
        if isinstance(data, dict):
            grant_type = data.get('grant_type')
            if grant_type == 'password':
                if not data.get('identifier') or not data.get('password'):
                    raise ValueError('For "password" grant, "identifier" and "password" are required.')
            elif grant_type == 'verification_code':
                if not data.get('identifier') or not data.get('code'):
                    raise ValueError('For "verification_code" grant, "identifier" and "code" are required.')
            elif grant_type == 'oauth_wechat':
                if not data.get('oauth_code'):
                    raise ValueError('For "oauth_wechat" grant, "oauth_code" is required.')
        return data


# ==============================================================================
# 3. Output Schemas (API输出/响应模型)
# ==============================================================================

class UserRead(UserBase):
    """
    用于从API安全地返回用户信息的Schema。
    关键：它不包含 password_hash 等敏感字段。
    """
    uuid: str = Field(..., description="用户的全局唯一标识符")
    status: str = Field(..., description="用户账户状态")
    user_type: str = Field(..., description="用户账户类型")
    
    # Pydantic V2 的现代化配置方式
    # from_attributes=True 替代了旧的 orm_mode=True
    model_config = ConfigDict(
        from_attributes=True,
    )

class Token(BaseModel):
    """
    用于返回JWT访问令牌的Schema。
    """
    access_token: str
    token_type: str = "bearer"