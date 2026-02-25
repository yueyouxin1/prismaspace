# app/api/v1/identity.py

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.context import AppContext
from app.api.dependencies.context import PublicContextDep, AuthContextDep
from app.models.identity import User
from app.schemas.identity.user_schemas import UserCreate, UserRead, Token, TokenRequest
from app.services.identity.user_service import UserService
from app.services.exceptions import (
    EmailAlreadyExistsError,
    PhoneNumberExistsError,
    InvalidCredentialsError,
    ConfigurationError,
)
from app.schemas.common import JsonResponse

router = APIRouter()


@router.post(
    "/register",
    response_model=JsonResponse[UserRead],
    status_code=status.HTTP_201_CREATED,
    summary="User Registration",
    description="Create a new user account and its associated billing account."
)
async def register_user(
    user_in: UserCreate,
    context: AppContext = PublicContextDep
):
    """
    Handles the user registration process.
    """
    try:
        user_service = UserService(context)
        new_user = await user_service.register_user(user_create=user_in)
        return JsonResponse(data=new_user)

    except EmailAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except PhoneNumberExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except ConfigurationError as e:
        # This is a server-side configuration issue, so a 500 error is appropriate.
        # Log this error for administrators.
        print(f"FATAL CONFIGURATION ERROR: {e}") # Replace with actual logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error. Please contact support.",
        )
    except Exception as e:
        # Catch-all for unexpected errors during registration
        print(f"Unexpected error during registration: {e}") # Replace with actual logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred."
        )


@router.post(
    "/token",
    response_model=JsonResponse[Token],
    summary="Get Access Token",
    description="Authenticate and receive a JWT access token. Supports multiple grant types."
)
async def login_for_access_token(
    token_request: TokenRequest,
    context: AppContext = PublicContextDep
):
    """
    Handles user authentication and issues a JWT token.
    """
    try:
        user_service = UserService(context)
        access_token = await user_service.login_for_access_token(token_request=token_request)
        return JsonResponse(data=access_token)

    except InvalidCredentialsError:
        print("DEBUG: Caught InvalidCredentialsError!") # <--- 添加这行
        # For security reasons, always return a generic 401 for any authentication failure.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect identifier or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except NotImplementedError as e:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(e),
        )
    except Exception as e:
        print(f"Unexpected error during token generation: {e}") # Replace with actual logging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred during authentication."
        )

@router.get(
    "/users/me",
    response_model=JsonResponse[UserRead],
    summary="Get Current User",
    description="Get profile information for the currently authenticated user."
)
async def read_users_me(
    context: AppContext = AuthContextDep
):
    """
    Returns the profile of the logged-in user.
    """
    # 从 auth 上下文中获取 user
    return JsonResponse(data=UserRead.model_validate(context.actor))