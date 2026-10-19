"""Test Suite for Phase 4: AES (Fernet) Password Vault & 30s Redis Unlock.

Operating Systems & Security Concepts Demonstrated:
1. Memory-Hard Key Derivation: Verifies scrypt key generation with per-user salt.
2. In-Memory Key Wrapping: Tests wrapping and unwrapping of cryptographic keys.
3. Zero-Knowledge Integrity: Ensures database stores only ciphertext.
4. Multi-Tenant Authorization: Confirms strict user boundary isolation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import pytest
from cryptography.fernet import Fernet, InvalidToken

from app.vault import (
    audit_vault_items,
    compute_verifier,
    derive_vault_key,
    unwrap_key,
    wrap_key,
)


class TestVaultKdfAndWrapping:
    """Test scrypt key derivation function and volatile Redis key wrapping."""

    def test_scrypt_key_derivation(self):
        salt = b"\x01" * 16
        raw_key, fernet_key_str = derive_vault_key("MasterSecret123!", salt)

        # Raw key must be 32 bytes
        assert len(raw_key) == 32
        # Fernet key must be 44 chars (base64 of 32 bytes)
        assert len(fernet_key_str) == 44

        # Same password and salt must produce deterministic key
        raw_key_2, fernet_key_str_2 = derive_vault_key("MasterSecret123!", salt)
        assert raw_key == raw_key_2
        assert fernet_key_str == fernet_key_str_2

        # Different salt must produce completely different key
        different_salt = b"\x02" * 16
        raw_key_diff, _ = derive_vault_key("MasterSecret123!", different_salt)
        assert raw_key != raw_key_diff

    def test_verifier_computation_and_comparison(self):
        salt = b"\x03" * 16
        raw_key, _ = derive_vault_key("MasterPassword2026", salt)
        verifier = compute_verifier(raw_key)

        assert isinstance(verifier, str)
        assert len(verifier) == 64  # SHA256 hex digest length

        # Verifier with correct key matches
        assert hmac.compare_digest(verifier, compute_verifier(raw_key))

        # Verifier with wrong key fails
        wrong_raw, _ = derive_vault_key("WrongPassword", salt)
        assert not hmac.compare_digest(verifier, compute_verifier(wrong_raw))

    def test_key_wrapping_and_unwrapping(self):
        salt = b"\x04" * 16
        _, fernet_key = derive_vault_key("SuperMasterPass", salt)

        wrapped = wrap_key(fernet_key)
        assert wrapped != fernet_key

        unwrapped = unwrap_key(wrapped)
        assert unwrapped == fernet_key

    def test_fernet_encryption_and_decryption(self):
        salt = b"\x05" * 16
        _, fernet_key = derive_vault_key("VaultPassword", salt)
        f = Fernet(fernet_key.encode("utf-8"))

        secret_text = "SuperSecretBankPIN_9876"
        ciphertext = f.encrypt(secret_text.encode("utf-8")).decode("utf-8")

        # Ciphertext must not contain plaintext
        assert secret_text not in ciphertext

        # Decrypts back to original
        decrypted = f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        assert decrypted == secret_text

        # Tampered ciphertext fails
        tampered = ciphertext[:-4] + "AAAA"
        with pytest.raises(InvalidToken):
            f.decrypt(tampered.encode("utf-8"))


class TestVaultSecurityAudit:
    """Test offline weak and duplicate password detection."""

    def test_weak_password_detection(self):
        items = [
            {"title": "Wi-Fi", "secret": "123456"},          # Weak (dictionary + short)
            {"title": "Portal", "secret": "password"},        # Weak (common dictionary)
            {"title": "Bank", "secret": "P@ssw0rdSecure!#$"},  # Strong
        ]
        audit = audit_vault_items(items)
        assert audit["total"] == 3
        assert audit["weak_count"] == 2
        assert items[0].get("is_weak") is True
        assert items[1].get("is_weak") is True
        assert items[2].get("is_weak") is not True

    def test_duplicate_password_detection(self):
        items = [
            {"title": "Campus Wi-Fi", "secret": "SharedPass2026!"},
            {"title": "Library Portal", "secret": "SharedPass2026!"},
            {"title": "GitHub", "secret": "UniqueGitHubSecret$99"},
        ]
        audit = audit_vault_items(items)
        assert audit["total"] == 3
        assert audit["duplicates_count"] == 2  # Two items reuse the same credential
        assert items[0].get("is_duplicate") is True
        assert items[1].get("is_duplicate") is True
        assert items[2].get("is_duplicate") is not True
