"""
Encryption helper for storing per-user Betfair credentials at rest.

Uses Fernet (symmetric, authenticated encryption) from the
`cryptography` package. The key lives in a Railway environment
variable (FERNET_KEY) — never committed to a tracked file, same
pattern as ANTHROPIC_API_KEY.

Generate a key once with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
and set it as FERNET_KEY in Railway's environment variables.
"""

import os
from cryptography.fernet import Fernet, InvalidToken

_fernet = None


def _get_fernet():
    global _fernet
    if _fernet is None:
        key = os.environ.get('FERNET_KEY') or os.getenv('FERNET_KEY')
        if not key:
            raise RuntimeError(
                "FERNET_KEY not configured — required to encrypt/decrypt "
                "per-user Betfair credentials. Set it in Railway env vars."
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(plaintext):
    """Encrypt a string for storage. Returns None if plaintext is falsy
    (so empty/unset fields stay NULL rather than encrypting an empty
    string)."""
    if not plaintext:
        return None
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext):
    """Decrypt a stored value. Returns None if ciphertext is falsy or
    fails to decrypt (e.g. key rotated, corrupted data) rather than
    raising — callers should treat None as "no usable credential" and
    fall back to the shared default."""
    if not ciphertext:
        return None
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None
