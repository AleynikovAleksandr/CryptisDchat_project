"""Совместимость компактного кодека с официальной реализацией protobuf (grpcio-tools)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from app.proto import wire

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pb2(tmp_path_factory):
    grpc_tools = pytest.importorskip("grpc_tools.protoc")
    out = tmp_path_factory.mktemp("pb")
    rc = grpc_tools.main(["protoc", f"-I{ROOT / 'proto'}", f"--python_out={out}", str(ROOT / "proto" / "cryptis.proto")])
    assert rc == 0
    sys.path.insert(0, str(out))
    try:
        yield importlib.import_module("cryptis_pb2")
    finally:
        sys.path.remove(str(out))


SAMPLE = {
    "client_msg_id": "7d3f0b8e-0000-4000-8000-000000000001", "thread_id": "t" * 36,
    "key_epoch": 3, "sender_key_version": 2, "nonce": bytes(range(24)), "ciphertext": b"\x00\xff" * 40,
    "signature": b"\x01" * 64, "kind": "text", "reply_to_id": "", "attachment_id": "a" * 36,
    "search_tokens": ["0" * 32, "f" * 32], "push_preview": "Привет 👋", "forwarded_from_id": "",
}


def test_our_encoding_is_parsed_by_official_protobuf(pb2):
    frame = {"event": "message.new", "thread_id": "t1", "message_id": "m1", "seq": 2**40, "sender_id": "u",
             "ts": 1_700_000_000_000, "packet": SAMPLE, "json": '{"a":1}'}
    msg = pb2.WsFrame.FromString(wire.encode("WsFrame", frame))
    assert msg.seq == 2**40 and msg.packet.key_epoch == 3
    assert msg.packet.nonce == SAMPLE["nonce"] and list(msg.packet.search_tokens) == SAMPLE["search_tokens"]
    assert msg.packet.push_preview == "Привет 👋"


def test_official_encoding_is_parsed_by_our_codec(pb2):
    m = pb2.MessagePacket(**{k: v for k, v in SAMPLE.items()})
    decoded = wire.decode("MessagePacket", m.SerializeToString())
    assert decoded == SAMPLE


def test_unknown_fields_are_skipped():
    data = wire.encode("MessagePacket", {"kind": "text"}) + bytes([(15 << 3) | 0, 5])
    assert wire.decode("MessagePacket", data)["kind"] == "text"


def test_truncated_input_is_rejected():
    with pytest.raises(wire.ProtoError):
        wire.decode("MessagePacket", bytes([(6 << 3) | 2, 50, 1, 2]))
