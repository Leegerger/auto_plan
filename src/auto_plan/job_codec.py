"""Bounded canonical data encoding shared with credential-free compute children."""
import hashlib
import json
import zlib

PAYLOAD_LIMIT = 64 * 1024 * 1024
METADATA_LIMIT = 64 * 1024


def document(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    if len(data) > PAYLOAD_LIMIT:
        raise ValueError('计算数据超过传输上限')
    return data


def digest(value):
    return hashlib.sha256(document(value)).hexdigest()


def encode(value):
    return zlib.compress(document(value))


def decode(blob, sha=None):
    from .access import StoreUnavailable
    try:
        if hasattr(blob, 'size') and blob.size() > PAYLOAD_LIMIT:
            raise ValueError('Oversized compressed payload')
        data = blob.read() if hasattr(blob, 'read') else blob
        if len(data) > PAYLOAD_LIMIT:
            raise ValueError('Oversized compressed payload')
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(data, PAYLOAD_LIMIT + 1)
        if len(raw) > PAYLOAD_LIMIT or not decompressor.eof or decompressor.unused_data:
            raise ValueError('Invalid bounded payload')
        if sha and hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('Digest mismatch')
        return json.loads(raw)
    except (TypeError, ValueError, zlib.error, UnicodeError):
        raise StoreUnavailable from None
