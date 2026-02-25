# tests/core/test_security.py

import pytest
from datetime import timedelta
from jose import JWTError

from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    decode_token,
)

# ==============================================================================
# 1. Password Security Tests
# ==============================================================================

async def test_password_hashing():
    """
    Tests that get_password_hash produces a valid hash and that verify_password works correctly.
    """
    password = "mysecretpassword123"
    
    # 1. Test hashing
    hashed_password = get_password_hash(password)
    assert hashed_password is not None
    assert isinstance(hashed_password, str)
    assert password != hashed_password  # Ensure the password is not stored in plain text
    assert "$2b$" in hashed_password  # Check for bcrypt prefix

    # 2. Test successful verification
    assert verify_password(password, hashed_password) is True

    # 3. Test failed verification with wrong password
    assert verify_password("wrongpassword", hashed_password) is False
    
    # 4. Test failed verification with empty password
    assert verify_password("", hashed_password) is False

# ==============================================================================
# 2. JWT (Access Token) Tests
# ==============================================================================

async def test_jwt_creation_and_decoding():
    """
    Tests the full lifecycle of creating and decoding a valid JWT.
    """
    subject = "user_uuid_for_testing"
    
    # 1. Create a token
    token = create_access_token(subject=subject)
    assert token is not None
    assert isinstance(token, str)

    # 2. Decode the token
    payload = decode_token(token)
    assert payload is not None
    
    # 3. Verify the payload content
    assert "sub" in payload
    assert payload["sub"] == subject
    assert "exp" in payload
    assert isinstance(payload["exp"], int)


async def test_jwt_with_custom_expiry():
    """
    Tests that a token can be created with a custom expiration delta.
    """
    subject = "test_custom_expiry"
    # A short expiry for testing purposes
    expires_delta = timedelta(seconds=5) 
    
    token = create_access_token(subject=subject, expires_delta=expires_delta)
    payload = decode_token(token)
    
    assert payload["sub"] == subject


async def test_jwt_expired_token():
    """
    Tests that decoding an expired token correctly raises a JWTError.
    """
    subject = "test_expired"
    # Create a token that expired in the past
    expires_delta = timedelta(seconds=-10) 
    
    expired_token = create_access_token(subject=subject, expires_delta=expires_delta)

    # Use pytest.raises to assert that a specific exception is raised
    with pytest.raises(JWTError):
        decode_token(expired_token)


async def test_jwt_invalid_signature():
    """
    Tests that a token with a tampered or invalid signature raises a JWTError.
    """
    subject = "test_invalid_signature"
    token = create_access_token(subject=subject)
    
    # Tamper with the token by adding extra characters
    tampered_token = token + "invalid"

    with pytest.raises(JWTError):
        decode_token(tampered_token)

async def test_jwt_without_subject():
    """
    Tests creating a token with a non-string subject (e.g., an integer ID).
    Our create_access_token function should handle this by converting it to a string.
    """
    subject_int = 12345
    token = create_access_token(subject=subject_int)
    payload = decode_token(token)
    
    assert payload["sub"] == str(subject_int)