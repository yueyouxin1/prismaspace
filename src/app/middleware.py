# app/middleware.py

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

# [修正] 认证策略现在只从 request 中提取凭证，不访问数据库
def _extract_bearer_token(request: Request) -> None:
    """Strategy for extracting a JWT Bearer token and placing it in state."""
    token = request.headers.get('Authorization')
    if token and token.startswith("Bearer "):
        setattr(request.state, "token", token.split(" ")[1])

def _extract_api_key(request: Request) -> None:
    """Strategy for extracting an Api-Key and placing it in state."""
    api_key_value = request.headers.get('Api-Key')
    if api_key_value:
        setattr(request.state, "api_key", api_key_value)

class AuthenticationMiddleware(BaseHTTPMiddleware):
    # 策略现在是提取器
    AUTH_EXTRACTORS = [
        _extract_bearer_token,
        _extract_api_key,
    ]

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 为每个请求重置状态
        setattr(request.state, "token", None)
        setattr(request.state, "api_key", None)
        setattr(request.state, "auth", None)
        
        # 依次执行所有凭证提取器
        for extractor in self.AUTH_EXTRACTORS:
            extractor(request)
        
        response = await call_next(request)
        return response