# src/app/core/encryption.py

from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings
import logging

# [安全关键] 确保在启动时就检查密钥是否存在
if not settings.CREDENTIAL_ENCRYPTION_KEY:
    raise ValueError("CREDENTIAL_ENCRYPTION_KEY is not set in the environment. Cannot start the application.")

fernet = Fernet(settings.CREDENTIAL_ENCRYPTION_KEY.encode())

def encrypt(plaintext: str) -> str:
    """Encrypts a plaintext string."""
    if not plaintext:
        return ""
    return fernet.encrypt(plaintext.encode()).decode()

def decrypt(ciphertext: str) -> str:
    """
    Decrypts a ciphertext string.
    Raises:
        ValueError: If decryption fails due to an invalid key or corrupted data.
    """
    if not ciphertext:
        return ""
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # 这个异常会被上层的 _safe_decrypt 捕获和处理
        raise ValueError("Decryption failed. Invalid key or corrupted data.")