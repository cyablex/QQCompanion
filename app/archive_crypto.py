"""Password-protected, authenticated streaming archives for private migration."""

import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"DFYARCH\x01"
HEADER_SIZE = len(MAGIC) + 16 + 12
CHUNK = 1024 * 1024


def _key(password, salt):
    if len(password) < 12:
        raise ValueError("恢复口令至少需要12个字符。")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(
        password.encode("utf-8")
    )


def encrypt_file(source, target, password):
    """Write a new encrypted archive; never replace an existing backup."""
    source, target = Path(source), Path(target)
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    encryptor = Cipher(
        algorithms.AES(_key(password, salt)), modes.GCM(nonce)
    ).encryptor()
    encryptor.authenticate_additional_data(header)
    created = False
    try:
        with source.open("rb") as incoming, target.open("xb") as outgoing:
            created = True
            outgoing.write(header)
            while block := incoming.read(CHUNK):
                outgoing.write(encryptor.update(block))
            outgoing.write(encryptor.finalize())
            outgoing.write(encryptor.tag)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise


def decrypt_file(source, target, password):
    """Authenticate the entire archive before any restored file is installed."""
    source, target = Path(source), Path(target)
    created = False
    try:
        with source.open("rb") as incoming:
            header = incoming.read(HEADER_SIZE)
            size = source.stat().st_size - HEADER_SIZE - 16
            if len(header) != HEADER_SIZE or not header.startswith(MAGIC) or size < 0:
                raise ValueError("不是受支持的大肥鱼加密备份。")
            incoming.seek(-16, 2)
            tag = incoming.read(16)
            incoming.seek(HEADER_SIZE)
            salt, nonce = header[len(MAGIC) : len(MAGIC) + 16], header[-12:]
            decryptor = Cipher(
                algorithms.AES(_key(password, salt)), modes.GCM(nonce, tag)
            ).decryptor()
            decryptor.authenticate_additional_data(header)
            with target.open("xb") as outgoing:
                created = True
                remaining = size
                while remaining:
                    block = incoming.read(min(CHUNK, remaining))
                    if not block:
                        raise ValueError("备份文件不完整。")
                    outgoing.write(decryptor.update(block))
                    remaining -= len(block)
                outgoing.write(decryptor.finalize())
    except InvalidTag:
        if created:
            target.unlink(missing_ok=True)
        raise ValueError("恢复口令错误，或备份文件已损坏。") from None
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
