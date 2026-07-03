"""Identifier masking (وضع السرية) — reversible per-user PII masking codec.

Public API consumed by the Phase 3 wiring layer:

    from shared.privacy import (
        PrivacyCodec, encode, decode, audit, normalize_digits,
        StreamDecoder, PiiMappingStore,
        NewMapping, DecodeResult, TripwireEvent, AuditHit,
    )

Typical wiring::

    store = PiiMappingStore(service_role_client)
    codec = store.load_codec(user_id, enabled=masking_on)
    prompt = codec.encode(user_text)          # mask before the LLM sees it
    store.persist_new(user_id, codec.new_mappings, codec)   # save new fakes
    ...
    shown = codec.decode(llm_output).text     # restore (decode is always active)

See ``codec.py`` module docstring for the locked-rule pipeline and the resolved
ambiguities.
"""
from shared.privacy.codec import (
    AuditHit,
    DecodeResult,
    NewMapping,
    PrivacyCodec,
    TripwireEvent,
    audit,
    decode,
    encode,
    generate_email_fake,
    generate_number_fake,
    normalize_digits,
)
from shared.privacy.store import PiiMappingStore
from shared.privacy.stream import StreamDecoder

__all__ = [
    "PrivacyCodec",
    "encode",
    "decode",
    "audit",
    "normalize_digits",
    "StreamDecoder",
    "PiiMappingStore",
    "NewMapping",
    "DecodeResult",
    "TripwireEvent",
    "AuditHit",
    "generate_number_fake",
    "generate_email_fake",
]
