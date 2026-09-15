"""Ed25519 (RFC 8032) in pure Python, so a town can verify a credential unaided.

No dependency, and none of the side-channel hardening a signing service needs.
This is adequate for *verification*, which handles only public data. ``sign`` is
here to let the tests mint credentials; do not use it to run an issuer.
"""

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P


def _add(point, other):
    a = (point[1] - point[0]) * (other[1] - other[0]) % P
    b = (point[1] + point[0]) * (other[1] + other[0]) % P
    c = 2 * point[3] * other[3] * D % P
    e = 2 * point[2] * other[2] % P
    return ((b - a) * (e - c) % P, (e + c) * (b + a) % P,
            (e - c) * (e + c) % P, (b - a) * (b + a) % P)


def _mul(scalar, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _recover_x(y, sign):
    if y >= P:
        return None
    square = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(square, (P + 3) // 8, P)
    if (x * x - square) % P:
        x = x * pow(2, (P - 1) // 4, P) % P
    if (x * x - square) % P:
        return None
    if x == 0 and sign:
        return None
    return P - x if x & 1 != sign else x


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
G = (_GX, _GY, 1, _GX * _GY % P)


def _decompress(data):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


def _compress(point):
    inverse = pow(point[2], P - 2, P)
    x, y = point[0] * inverse % P, point[1] * inverse % P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _secret_scalar(secret):
    digest = hashlib.sha512(secret).digest()
    scalar = int.from_bytes(digest[:32], "little")
    return (scalar & ((1 << 254) - 8)) | (1 << 254), digest[32:]


def public_key(secret):
    scalar, _ = _secret_scalar(secret)
    return _compress(_mul(scalar, G))


def sign(secret, message):
    scalar, prefix = _secret_scalar(secret)
    encoded = _compress(_mul(scalar, G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % L
    commitment = _compress(_mul(r, G))
    challenge = int.from_bytes(
        hashlib.sha512(commitment + encoded + message).digest(), "little"
    ) % L
    return commitment + int.to_bytes((r + challenge * scalar) % L, 32, "little")


def verify(signature, message, key):
    """True only for a signature this key actually produced over this message."""
    if not isinstance(signature, bytes) or len(signature) != 64 or len(key) != 32:
        return False
    commitment, encoded = _decompress(signature[:32]), _decompress(key)
    if commitment is None or encoded is None:
        return False
    scalar = int.from_bytes(signature[32:], "little")
    if scalar >= L:  # reject malleable signatures
        return False
    challenge = int.from_bytes(
        hashlib.sha512(signature[:32] + key + message).digest(), "little"
    ) % L
    left, right = _mul(scalar, G), _add(commitment, _mul(challenge, encoded))
    return (left[0] * right[2] - right[0] * left[2]) % P == 0 and (
        left[1] * right[2] - right[1] * left[2]
    ) % P == 0
