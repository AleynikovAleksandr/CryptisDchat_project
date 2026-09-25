"""Минимальный кодек wire-формата Protocol Buffers для схемы proto/cryptis.proto.

Поддерживаются ровно те типы, что нужны схеме: string, bytes, uint32, uint64,
вложенное сообщение и repeated string. Неизвестные поля при разборе пропускаются
(прямая совместимость, как у штатного protobuf).
"""
from __future__ import annotations

from typing import Any

# поле: (номер, имя, тип, repeated)
SCHEMA: dict[str, list[tuple[int, str, str, bool]]] = {
    "MessagePacket": [
        (1, "client_msg_id", "string", False),
        (2, "thread_id", "string", False),
        (3, "key_epoch", "uint32", False),
        (4, "sender_key_version", "uint32", False),
        (5, "nonce", "bytes", False),
        (6, "ciphertext", "bytes", False),
        (7, "signature", "bytes", False),
        (8, "kind", "string", False),
        (9, "reply_to_id", "string", False),
        (10, "attachment_id", "string", False),
        (11, "search_tokens", "string", True),
        (12, "push_preview", "string", False),
        (13, "forwarded_from_id", "string", False),
    ],
    "WsFrame": [
        (1, "event", "string", False),
        (2, "thread_id", "string", False),
        (3, "message_id", "string", False),
        (4, "seq", "uint64", False),
        (5, "sender_id", "string", False),
        (6, "ts", "uint64", False),
        (7, "packet", "message:MessagePacket", False),
        (8, "json", "string", False),
    ],
}

_VARINT = 0
_LEN = 2
_I64 = 1
_I32 = 5


class ProtoError(ValueError):
    pass


def _enc_varint(value: int, out: bytearray) -> None:
    if value < 0:
        raise ProtoError("negative varint")
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def _dec_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ProtoError("truncated varint")
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ProtoError("varint too long")


def encode(msg_type: str, data: dict[str, Any]) -> bytes:
    out = bytearray()
    for num, name, ftype, repeated in SCHEMA[msg_type]:
        value = data.get(name)
        if value is None:
            continue
        values = value if repeated else [value]
        for v in values:
            if ftype in ("uint32", "uint64"):
                if not v:
                    continue  # proto3: значения по умолчанию не пишутся
                _enc_varint((num << 3) | _VARINT, out)
                _enc_varint(int(v), out)
                continue
            if ftype == "string":
                if v == "" and not repeated:
                    continue
                payload = str(v).encode("utf-8")
            elif ftype == "bytes":
                if not v:
                    continue
                payload = bytes(v)
            elif ftype.startswith("message:"):
                payload = encode(ftype.split(":", 1)[1], v)
            else:  # pragma: no cover - схема статична
                raise ProtoError(f"unsupported type {ftype}")
            _enc_varint((num << 3) | _LEN, out)
            _enc_varint(len(payload), out)
            out += payload
    return bytes(out)


def decode(msg_type: str, buf: bytes) -> dict[str, Any]:
    fields = {num: (name, ftype, repeated) for num, name, ftype, repeated in SCHEMA[msg_type]}
    result: dict[str, Any] = {}
    for _, name, ftype, repeated in SCHEMA[msg_type]:
        if repeated:
            result[name] = []
        elif ftype in ("uint32", "uint64"):
            result[name] = 0
        elif ftype == "string":
            result[name] = ""
        elif ftype == "bytes":
            result[name] = b""
        else:
            result[name] = None
    pos = 0
    while pos < len(buf):
        key, pos = _dec_varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == _VARINT:
            value, pos = _dec_varint(buf, pos)
        elif wire == _LEN:
            length, pos = _dec_varint(buf, pos)
            if pos + length > len(buf):
                raise ProtoError("truncated field")
            value = buf[pos:pos + length]
            pos += length
        elif wire == _I64:
            pos += 8
            continue
        elif wire == _I32:
            pos += 4
            continue
        else:
            raise ProtoError(f"unsupported wire type {wire}")
        if num not in fields:
            continue
        name, ftype, repeated = fields[num]
        if ftype in ("uint32", "uint64"):
            if wire != _VARINT:
                raise ProtoError(f"field {name}: expected varint")
            decoded: Any = value
        else:
            if wire != _LEN:
                raise ProtoError(f"field {name}: expected length-delimited")
            if ftype == "string":
                try:
                    decoded = bytes(value).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ProtoError(f"field {name}: invalid utf-8") from exc
            elif ftype == "bytes":
                decoded = bytes(value)
            else:
                decoded = decode(ftype.split(":", 1)[1], bytes(value))
        if repeated:
            result[name].append(decoded)
        else:
            result[name] = decoded
    return result
