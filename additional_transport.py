"""Additional-story transport using the existing one-run authenticated envelope.

The jobs-shaped wrapper is solely a transport container, never the four-job
editorial contract. Its single element has an independently versioned schema.
Recovery artifacts are encrypted to a separate one-run recipient public key.
"""
import base64
import io
import json
import os
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import staged_transfer as t
import additional_batch as a

MAX_RECOVERY_BYTES = 256 * 1024 * 1024


def validate_recovery_key(recipient_public_key):
    t.unb64(recipient_public_key, 32)
    # Reject low-order/null peers before any paid work can need recovery.
    t.derive(X25519PrivateKey.generate(), recipient_public_key,
             {'purpose': 'additional-recovery-key-validation-v1'})


def wrap_payload(packet, approval, verified_tts_terms=None, *, continuation_evidence=None):
    payload = {'additional_version': 1, 'packet': packet, 'approval': approval,
               'verified_tts_terms': verified_tts_terms}
    if continuation_evidence is not None:
        payload['continuation_evidence'] = continuation_evidence
    return {'jobs': [payload]}


def unwrap_payload(value):
    a.require(isinstance(value, dict) and set(value) == {'jobs'} and isinstance(value['jobs'], list)
              and len(value['jobs']) == 1, 'Invalid additional transport wrapper')
    payload = value['jobs'][0]
    a.require(isinstance(payload, dict) and set(payload) in (
              {'additional_version', 'packet', 'approval', 'verified_tts_terms'},
              {'additional_version', 'packet', 'approval', 'verified_tts_terms', 'continuation_evidence'}) and
              type(payload['additional_version']) is int and payload['additional_version'] == 1
              and isinstance(payload['packet'], dict) and isinstance(payload['approval'], dict)
              and (payload['verified_tts_terms'] is None or isinstance(payload['verified_tts_terms'], dict)),
              'Invalid additional payload')
    if 'continuation_evidence' in payload:
        a.require(isinstance(payload['continuation_evidence'], dict), 'Invalid continuation evidence')
    return payload


def encrypt_recovery(root, destination, recipient_public_key, context):
    """Encrypt only this batch's explicit run files. Never archive credentials/state dirs."""
    validate_recovery_key(recipient_public_key)
    root = Path(root).resolve(); destination = Path(destination)
    files = []
    if root.exists():
        for path in sorted(root.rglob('*')):
            a.require(not path.is_symlink(), 'Symlink in recovery artifact')
            if path.is_file():
                a.require(path.resolve().is_relative_to(root), 'Unsafe recovery path')
                files.append(path)
    a.require(sum(p.stat().st_size for p in files) <= MAX_RECOVERY_BYTES, 'Recovery artifact too large')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files: archive.write(path, str(path.relative_to(root)))
    raw = buffer.getvalue()
    a.require(len(raw) <= MAX_RECOVERY_BYTES, 'Recovery archive too large')
    private = X25519PrivateKey.generate()
    bound = {'purpose': 'additional-story-recovery-v1', **context,
             'recipient_public_key': recipient_public_key, 'ephemeral_public_key': t.public(private)}
    nonce = os.urandom(12)
    encrypted = AESGCM(t.derive(private, recipient_public_key, bound)).encrypt(nonce, raw, t.canonical(bound))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # No raw ZIP or private key is written to an uploaded path.
    destination.write_bytes(t.canonical({'context': bound, 'nonce': t.b64(nonce), 'ciphertext': t.b64(encrypted)}))
    return {'files': len(files), 'ciphertext_sha256': a.hashlib.sha256(destination.read_bytes()).hexdigest()}


def decrypt_recovery(raw, recipient_private, expected_context):
    """Offline operator utility; returns archive bytes, never extracts arbitrary paths."""
    a.require(isinstance(raw, bytes) and len(raw) <= 2 * MAX_RECOVERY_BYTES, 'Invalid recovery envelope')
    envelope = json.loads(raw)
    a.require(isinstance(envelope, dict) and set(envelope) == {'context', 'nonce', 'ciphertext'},
              'Invalid recovery fields')
    bound = envelope['context']
    a.require(isinstance(bound, dict) and bound == {
        'purpose': 'additional-story-recovery-v1', **expected_context,
        'recipient_public_key': t.public(recipient_private),
        'ephemeral_public_key': bound.get('ephemeral_public_key')}, 'Recovery context mismatch')
    # Transport's 128-KiB decoder is deliberately not used for large artifacts.
    ciphertext = base64.b64decode(envelope['ciphertext'], validate=True)
    a.require(len(ciphertext) <= MAX_RECOVERY_BYTES + 16, 'Recovery ciphertext too large')
    return AESGCM(t.derive(recipient_private, bound['ephemeral_public_key'], bound)).decrypt(
        t.unb64(envelope['nonce'], 12), ciphertext, t.canonical(bound))
