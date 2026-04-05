"""AES-256-GCM encrypted credential storage."""
from __future__ import annotations

import base64
import logging
import os
import secrets
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.config import settings
from backend.credential.models import (
    Credential,
    CredentialCreate,
    CredentialResponse,
    CredentialUpdate,
)

logger = logging.getLogger(__name__)

_NONCE_SIZE = 12  # 96-bit nonce for AES-GCM


class CredentialVault:
    """Encrypt/decrypt credentials. Storage via get_repository('credentials')."""

    def __init__(self):
        key_hex = settings.credential_key
        if not key_hex:
            key_hex = self._generate_and_save_key()
        self._key = bytes.fromhex(key_hex)
        if len(self._key) != 32:
            raise ValueError("CREDENTIAL_KEY must be 64 hex chars (32 bytes)")
        self._aesgcm = AESGCM(self._key)

    @staticmethod
    def _generate_and_save_key() -> str:
        """Generate a random 256-bit key and append to .env."""
        key_hex = secrets.token_hex(32)
        env_path = Path(".env")
        try:
            with open(env_path, "a") as f:
                f.write(f"\nCREDENTIAL_KEY={key_hex}\n")
            logger.info("Generated new CREDENTIAL_KEY and saved to .env")
        except OSError:
            logger.warning("Could not write CREDENTIAL_KEY to .env — set it manually")
        os.environ["CREDENTIAL_KEY"] = key_hex
        settings.credential_key = key_hex
        return key_hex

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext → base64(nonce + ciphertext)."""
        nonce = secrets.token_bytes(_NONCE_SIZE)
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        """Decrypt base64(nonce + ciphertext) → plaintext."""
        from cryptography.exceptions import InvalidTag
        raw = base64.b64decode(encrypted)
        nonce = raw[:_NONCE_SIZE]
        ct = raw[_NONCE_SIZE:]
        try:
            return self._aesgcm.decrypt(nonce, ct, None).decode("utf-8")
        except InvalidTag:
            raise ValueError("Failed to decrypt credential — key may have changed")

    async def create(self, user_id: str, data: CredentialCreate) -> CredentialResponse:
        from backend.storage import get_repository
        repo = get_repository("credentials")

        cred = Credential(
            name=data.name,
            username=data.username,
            encrypted_password=self.encrypt(data.password),
            domain=data.domain,
            user_id=user_id,
        )
        doc = cred.model_dump()
        doc["_id"] = doc.pop("id")
        await repo.insert_one(doc)
        return CredentialResponse(**{**doc, "id": doc["_id"]})

    async def list_for_user(self, user_id: str) -> List[CredentialResponse]:
        from backend.storage import get_repository
        repo = get_repository("credentials")

        docs = await repo.find_many(
            {"user_id": user_id},
            sort=[("created_at", -1)],
        )
        return [
            CredentialResponse(**{**d, "id": d["_id"]})
            for d in docs
        ]

    async def update(
        self, user_id: str, cred_id: str, data: CredentialUpdate
    ) -> Optional[CredentialResponse]:
        from backend.storage import get_repository
        repo = get_repository("credentials")

        existing = await repo.find_one({"_id": cred_id, "user_id": user_id})
        if not existing:
            return None

        updates: dict = {"updated_at": datetime.now()}
        if data.name is not None:
            updates["name"] = data.name
        if data.username is not None:
            updates["username"] = data.username
        if data.password:
            updates["encrypted_password"] = self.encrypt(data.password)
        if data.domain is not None:
            updates["domain"] = data.domain

        await repo.update_one({"_id": cred_id}, {"$set": updates})
        doc = await repo.find_one({"_id": cred_id})
        return CredentialResponse(**{**doc, "id": doc["_id"]}) if doc else None

    async def delete(self, user_id: str, cred_id: str) -> bool:
        from backend.storage import get_repository
        repo = get_repository("credentials")
        count = await repo.delete_one({"_id": cred_id, "user_id": user_id})
        return count > 0

    async def decrypt_credential(self, user_id: str, cred_id: str) -> Optional[str]:
        """Decrypt and return the plaintext password. Internal use only."""
        from backend.storage import get_repository
        repo = get_repository("credentials")
        doc = await repo.find_one({"_id": cred_id, "user_id": user_id})
        if not doc:
            return None
        return self.decrypt(doc["encrypted_password"])


async def inject_credentials(user_id: str, params: dict, kwargs: dict) -> dict:
    """Resolve credential_id references in params and inject into kwargs.

    Args:
        user_id: The user who owns the credentials.
        params: Parameter config dict, e.g. {"password": {"sensitive": true, "credential_id": "cred_xxx"}}.
        kwargs: The kwargs dict that will be passed to execute_skill.

    Returns:
        Updated kwargs with decrypted credential values injected.
    """
    vault = get_vault()
    result = dict(kwargs)
    for param_name, param_info in params.items():
        if not isinstance(param_info, dict):
            continue
        cred_id = param_info.get("credential_id")
        if not cred_id:
            continue
        plaintext = await vault.decrypt_credential(user_id, cred_id)
        if plaintext is not None:
            result[param_name] = plaintext
    return result


_vault: Optional[CredentialVault] = None


def get_vault() -> CredentialVault:
    global _vault
    if _vault is None:
        _vault = CredentialVault()
    return _vault
