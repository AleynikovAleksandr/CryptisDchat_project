"""Хэши событий для блокчейн-журнала (ТЗ 2).

Хэш детерминированно вычисляется из метаданных события: отправитель/получатель,
чат, номер сообщения, время и SHA-256 шифротекста. Сам шифротекст и тем более
открытый текст в блокчейн не попадают.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime


def _digest(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()


def message_sent_hash(message_id: str, thread_id: str, sender_id: str, seq: int, content_hash: str, ts: datetime) -> str:
    return _digest({
        "v": 1, "type": "message.sent", "message_id": message_id, "thread_id": thread_id,
        "sender": sender_id, "seq": seq, "content": content_hash, "ts": ts.isoformat(),
    })


def receipt_hash(event_type: str, thread_id: str, actor_id: str, up_to_seq: int, ts: datetime) -> str:
    return _digest({
        "v": 1, "type": event_type, "thread_id": thread_id, "actor": actor_id,
        "up_to_seq": up_to_seq, "ts": ts.isoformat(),
    })


def membership_hash(event_id: str, thread_id: str, actor_id: str, action: str,
                    subject_id: str | None, ts: datetime) -> str:
    return _digest({
        "v": 1, "type": "group.membership", "event_id": event_id, "thread_id": thread_id,
        "actor": actor_id, "action": action, "subject": subject_id, "ts": ts.isoformat(),
    })
