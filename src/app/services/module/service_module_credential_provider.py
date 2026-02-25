# src/app/services/module/service_module_credential_provider.py

import logging
from typing import Optional, Dict
from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.encryption import decrypt
from app.models import Workspace, ServiceModule, ServiceModuleProvider
from app.dao.module.service_module_credential_dao import ServiceModuleCredentialDao
from .types.credential import ResolvedCredential

class ServiceModuleCredentialProvider:
    """
    Resolves plaintext API keys based on an explicit Workspace context.
    It follows a "bubble-up" strategy from specific (Workspace) to general (Platform).
    """
    def __init__(self, db: AsyncSession):
        self.dao = ServiceModuleCredentialDao(db)
        # Request-level cache to avoid redundant DB queries within the same operation
        self._cache: Dict[str, Optional[ResolvedCredential]] = {}

    def _get_cache_key(self, provider_id: int, workspace_id: int) -> str:
        """Generates a unique request-level cache key from the explicit context."""
        return f"cred:{provider_id}:ws:{workspace_id}"

    def _safe_decrypt(self, encrypted_value: Optional[str], context_info: str) -> Optional[str]:
        """Safely handles decryption failures without crashing."""
        if not encrypted_value:
            return None
        try:
            return decrypt(encrypted_value)
        except ValueError:
            logging.warning(
                f"Failed to decrypt credential value for {context_info}. "
                "The stored value might be corrupted or the encryption key has changed."
            )
            return None

    def _get_platform_credential(self, provider: ServiceModuleProvider) -> Optional[ResolvedCredential]:
        """
        Gets the fallback credential from platform environment variables.
        This is now more structured to build a ResolvedCredential object.
        """
        api_key_var = f"{provider.name.upper()}_API_KEY"
        endpoint_var = f"{provider.name.upper()}_API_URL" # e.g., OPENAI_API_URL
        
        api_key = getattr(settings, api_key_var, None)
        if not api_key:
            return None
            
        endpoint = getattr(settings, endpoint_var, None)
        
        return ResolvedCredential(
            api_key=api_key,
            endpoint=HttpUrl(endpoint) if endpoint else None,
            # Platform-level region/attributes can be added here if needed
            is_custom=False
        )

    async def get_credential(
        self, 
        service_module: ServiceModule, 
        workspace: Workspace
    ) -> Optional[ResolvedCredential]:
        """
        [V4.2 CORE METHOD] Resolves the API key using a two-level bubble-up strategy:
        1. Workspace-specific credential
        2. Platform-level default credential
        
        Args:
            provider: The identifier of the service provider.
            workspace: The fully loaded Workspace ORM object representing the current context.
            
        Returns:
            The plaintext API key as a string, or None if no valid credential is found.
        """
            
        provider = service_module.provider

        if not provider:
            return None

        cache_key = self._get_cache_key(provider.id, workspace.id)
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        resolved_cred: Optional[ResolvedCredential] = None

        # --- Level 1: Workspace-Specific Credential ---
        db_cred = await self.dao.get_by_workspace_and_provider(
            provider_id=provider.id, 
            workspace_id=workspace.id
        )
        if db_cred:
            api_key = self._safe_decrypt(db_cred.encrypted_value, f"provider '{provider.name}' in workspace '{workspace.id}'")
            if api_key: # 只有当API Key成功解密时，这个凭证才算有效
                endpoint_str = self._safe_decrypt(db_cred.encrypted_endpoint, f"endpoint for '{provider.name}' in workspace '{workspace.id}'")
                resolved_cred = ResolvedCredential(
                    api_key=api_key,
                    endpoint=HttpUrl(endpoint_str) if endpoint_str else None,
                    region=db_cred.region,
                    attributes=db_cred.attributes or {},
                    is_custom=True
                )

        # --- Level 2: Platform-Level Fallback ---
        if resolved_cred is None:
            resolved_cred = self._get_platform_credential(provider)

        # Cache the final result (even if it's None) and return
        self._cache[cache_key] = resolved_cred
        return resolved_cred