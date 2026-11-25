"""Independent, test-only SecureCRT decoder primitives; never used by the runner."""

import hashlib
from functools import lru_cache

from ._blowfish_constants import PI_HEX

MASK = 0xffffffff


class Blowfish:
    def __init__(self, key, salt=None):
        words = [int(PI_HEX[i:i + 8], 16) for i in range(0, len(PI_HEX), 8)]
        self.p = words[:18]
        self.s = [words[18 + i * 256:18 + (i + 1) * 256] for i in range(4)]
        self.expand(key, salt)

    def f(self, value):
        a, b, c, d = value >> 24, (value >> 16) & 255, (value >> 8) & 255, value & 255
        return (((self.s[0][a] + self.s[1][b]) & MASK) ^ self.s[2][c]) + self.s[3][d] & MASK

    def block(self, left, right, decrypt=False):
        p = self.p[::-1] if decrypt else self.p
        for i in range(16):
            left ^= p[i]
            right ^= self.f(left)
            left, right = right, left
        left, right = right, left
        right ^= p[16]
        left ^= p[17]
        return left, right

    def expand(self, key, salt=None):
        def word(data, index):
            return int.from_bytes(bytes(data[(index + n) % len(data)] for n in range(4)), "big")
        for i in range(18):
            self.p[i] ^= word(key, i * 4)
        left = right = offset = 0
        for table in (self.p, *self.s):
            for i in range(0, len(table), 2):
                if salt:
                    left ^= word(salt, offset)
                    right ^= word(salt, offset + 4)
                    offset += 8
                left, right = self.block(left, right)
                table[i], table[i + 1] = left, right

    def decrypt_cbc(self, data):
        if not data or len(data) % 8:
            raise ValueError("invalid Blowfish length")
        out, iv = bytearray(), bytes(8)
        for offset in range(0, len(data), 8):
            chunk = data[offset:offset + 8]
            left, right = self.block(int.from_bytes(chunk[:4], "big"), int.from_bytes(chunk[4:], "big"), True)
            plain = left.to_bytes(4, "big") + right.to_bytes(4, "big")
            out.extend(a ^ b for a, b in zip(plain, iv))
            iv = chunk
        return bytes(out)


@lru_cache(maxsize=8)
def bcrypt_pbkdf_empty(salt, rounds=16, length=48):
    """bcrypt_pbkdf with the empty configuration passphrase allowed by SecureCRT."""
    password = hashlib.sha512(b"").digest()
    blocks = (length + 31) // 32
    result = bytearray(blocks * 32)
    for block_number in range(1, blocks + 1):
        current_salt = hashlib.sha512(salt + block_number.to_bytes(4, "big")).digest()
        aggregate = bytearray(32)
        for _ in range(rounds):
            cipher = Blowfish(password, current_salt)
            for _ in range(64):
                cipher.expand(current_salt)
                cipher.expand(password)
            output = bytearray(b"OxychromaticBlowfishSwatDynamite")
            for offset in range(0, 32, 8):
                left, right = int.from_bytes(output[offset:offset + 4], "big"), int.from_bytes(output[offset + 4:offset + 8], "big")
                for _ in range(64):
                    left, right = cipher.block(left, right)
                output[offset:offset + 8] = left.to_bytes(4, "big") + right.to_bytes(4, "big")
            digest = b"".join(output[i:i + 4][::-1] for i in range(0, 32, 4))
            for i, byte in enumerate(digest):
                aggregate[i] ^= byte
            current_salt = hashlib.sha512(digest).digest()
        for i, byte in enumerate(aggregate):
            result[i * blocks + block_number - 1] = byte
    return bytes(result[:length])


def _mul(a, b):
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        a = ((a << 1) ^ (0x11b if a & 0x80 else 0)) & 255
        b >>= 1
    return result


def _power(value, exponent):
    result = 1
    while exponent:
        if exponent & 1:
            result = _mul(result, value)
        value = _mul(value, value)
        exponent >>= 1
    return result


def _rotate(byte, amount):
    return ((byte << amount) | (byte >> (8 - amount))) & 255


def _sbox(value):
    inv = _power(value, 254) if value else 0
    return inv ^ _rotate(inv, 1) ^ _rotate(inv, 2) ^ _rotate(inv, 3) ^ _rotate(inv, 4) ^ 0x63


SBOX = tuple(_sbox(i) for i in range(256))
INV_SBOX = tuple(SBOX.index(i) for i in range(256))


def _aes_keys(key):
    if len(key) != 32:
        raise ValueError("AES-256 key required")
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    rcon = 1
    for i in range(8, 60):
        item = words[-1][:]
        if i % 8 == 0:
            item = [SBOX[x] for x in item[1:] + item[:1]]
            item[0] ^= rcon
            rcon = _mul(rcon, 2)
        elif i % 8 == 4:
            item = [SBOX[x] for x in item]
        words.append([a ^ b for a, b in zip(words[i - 8], item)])
    return [[byte for word in words[i:i + 4] for byte in word] for i in range(0, 60, 4)]


def _aes_decrypt_block(block, keys):
    state = [a ^ b for a, b in zip(block, keys[14])]
    for round_number in range(13, -1, -1):
        state = [state[4 * ((col - row) % 4) + row] for col in range(4) for row in range(4)]
        state = [INV_SBOX[value] ^ key for value, key in zip(state, keys[round_number])]
        if round_number:
            mixed = []
            for col in range(4):
                a, b, c, d = state[4 * col:4 * col + 4]
                mixed.extend((_mul(a, 14) ^ _mul(b, 11) ^ _mul(c, 13) ^ _mul(d, 9),
                              _mul(a, 9) ^ _mul(b, 14) ^ _mul(c, 11) ^ _mul(d, 13),
                              _mul(a, 13) ^ _mul(b, 9) ^ _mul(c, 14) ^ _mul(d, 11),
                              _mul(a, 11) ^ _mul(b, 13) ^ _mul(c, 9) ^ _mul(d, 14)))
            state = mixed
    return bytes(state)


def aes_cbc_decrypt(key, iv, data):
    if not data or len(data) % 16 or len(iv) != 16:
        raise ValueError("invalid AES framing")
    keys = _aes_keys(key)
    out = bytearray()
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        out.extend(a ^ b for a, b in zip(_aes_decrypt_block(chunk, keys), iv))
        iv = chunk
    return bytes(out)


def decode_v2(data, prefix):
    if prefix == "02":
        key, iv = hashlib.sha256(b"").digest(), bytes(16)
    elif prefix == "03":
        if len(data) < 32:
            raise ValueError("missing salt or ciphertext")
        derived = bcrypt_pbkdf_empty(data[:16])
        key, iv, data = derived[:32], derived[32:], data[16:]
    else:
        raise ValueError("unsupported prefix")
    decoded = aes_cbc_decrypt(key, iv, data)
    length = int.from_bytes(decoded[:4], "little")
    if length > len(decoded) - 36:
        raise ValueError("invalid length")
    secret, digest, padding = decoded[4:4 + length], decoded[4 + length:36 + length], decoded[36 + length:]
    expected = 16 - (36 + length) % 16
    if expected < 8:
        expected += 16
    if len(padding) != expected or hashlib.sha256(secret).digest() != digest:
        raise ValueError("invalid framing or digest")
    return secret.decode("utf-8")


def decode_legacy(data):
    key1 = bytes.fromhex("24a63dde5bd3b3829c7e06f40816aa07")
    key2 = bytes.fromhex("5fb045a29417d916c6c6a2ff064182b7")
    outer = Blowfish(key1).decrypt_cbc(data)
    if len(outer) <= 8:
        raise ValueError("short legacy value")
    plain = Blowfish(key2).decrypt_cbc(outer[4:-4])
    end = next((i for i in range(0, len(plain), 2) if plain[i:i + 2] == b"\0\0"), None)
    if end is None or len(plain) - end - 2 != 8 - (end + 2) % 8:
        raise ValueError("invalid legacy framing")
    return plain[:end].decode("utf-16le")
