# app/core/security.py

from datetime import datetime, timedelta
from typing import Any, Union
import hashlib
import hmac
from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import settings

# ------------------------------------------------------------------------------
# 1. Password Hashing
#    - We use passlib's CryptContext for robust password management.
#    - bcrypt is the chosen scheme due to its strength against brute-force attacks.
# ------------------------------------------------------------------------------

# Create a CryptContext instance, specifying bcrypt as the hashing algorithm.
# "deprecated="auto"" will automatically handle hash upgrades in the future if we change schemes.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifies a plain-text password against a hashed password.

    :param plain_password: The password to verify.
    :param hashed_password: The stored hashed password.
    :return: True if the passwords match, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hashes a plain-text password using bcrypt.

    :param password: The password to hash.
    :return: The hashed password string.
    """
    return pwd_context.hash(password)


# ------------------------------------------------------------------------------
# 2. JSON Web Token (JWT) Management
#    - Handles the creation and verification of access tokens for authentication.
# ------------------------------------------------------------------------------

def create_access_token(subject: Union[str, Any], expires_delta: timedelta = None) -> str:
    """
    Creates a new JWT access token.

    :param subject: The subject of the token (e.g., user ID or UUID). This will be encoded in the 'sub' claim.
    :param expires_delta: Optional timedelta for token expiration. If None, uses default from settings.
    :return: The encoded JWT string.
    """
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    # Data to be encoded in the token payload
    to_encode = {
        "exp": expire,
        "sub": str(subject)  # 'sub' is the standard claim for subject identifier
    }
    
    # Encode the payload with our secret key and algorithm
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_token(token: str) -> dict | None:
    """
    Decodes and verifies a JWT access token.

    This function handles signature verification, expiration check, and algorithm validation.

    :param token: The JWT string to decode.
    :return: The decoded payload as a dictionary if the token is valid, otherwise None.
             The caller (e.g., middleware) is responsible for handling the None case
             (e.g., by raising an authentication exception).
    :raises JWTError: Propagates exceptions from the jose library for invalid tokens
                      (e.g., expired signature, invalid signature).
    """
    try:
        # The jwt.decode function automatically handles signature, expiration, and algorithm checks.
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        return payload
    except JWTError:
        # If any validation fails (expired, invalid signature, etc.), JWTError is raised.
        # We let this exception propagate upwards to be handled by the middleware or dependency.
        raise

def get_api_key_hash(api_key_secret: str) -> str:
    """Hashes the secret part of an API key."""
    return hashlib.sha256(api_key_secret.encode()).hexdigest()

def verify_api_key_hash(api_key_secret: str, stored_hash: str) -> bool:
    """Verifies a plain API key secret against a stored hash in constant time."""
    return hmac.compare_digest(get_api_key_hash(api_key_secret), stored_hash)