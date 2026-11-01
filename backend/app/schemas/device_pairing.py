"""Public metadata and opaque encrypted envelopes for browser pairing."""
from __future__ import annotations

import base64
import binascii
from typing import Annotated

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

PairingID = Annotated[str, StringConstraints(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')]


class PairingModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PublicKey(PairingModel):
    ephemeral_public_key: str = Field(min_length=124, max_length=124)

    @field_validator('ephemeral_public_key')
    @classmethod
    def valid_key(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
            key = serialization.load_der_public_key(decoded)
            if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
                raise ValueError('expected a P-256 public key')
            canonical = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            if len(decoded) != 91 or canonical != decoded or base64.b64encode(canonical).decode() != value:
                raise ValueError('expected canonical P-256 SPKI')
        except (ValueError, binascii.Error, UnsupportedAlgorithm) as exc:
            raise ValueError('expected a canonical base64 P-256 SPKI public key') from exc
        return value


class PairingCreateIn(PublicKey):
    device_name: str = Field(min_length=1, max_length=100)

    @field_validator('device_name')
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip() or any(ord(ch) < 32 for ch in value):
            raise ValueError('expected a device label')
        return value.strip()


class PairingScanIn(PublicKey):
    request_id: PairingID


class PairingEnvelope(PairingScanIn):
    account_id: PairingID
    operation_id: PairingID
    ciphertext: str = Field(min_length=128, max_length=1_048_576)

    @field_validator('ciphertext')
    @classmethod
    def sealed_payload(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('expected base64 ciphertext') from exc
        # CXC.eciesSeal: version || uncompressed P-256 point || IV || GCM tag/body.
        if len(decoded) < 1 + 65 + 12 + 16 or decoded[0] != 1:
            raise ValueError('expected a version-1 ECIES blob of at least 94 bytes')
        try:
            ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded[1:66])
        except ValueError as exc:
            raise ValueError('expected a valid uncompressed P-256 ECIES point') from exc
        return value


class PairingApproveIn(PairingScanIn):
    envelope: PairingEnvelope


class PairingCompleteIn(PairingModel):
    operation_id: PairingID
