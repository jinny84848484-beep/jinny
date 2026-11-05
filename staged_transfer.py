"""Single-use signed, encrypted input for the explicit staged production mode.

The receiver retains its private key in memory only. This protects plaintext in
public transit, not against a compromised runner or repository maintainer.
The receiver trusts the signing public key pinned by authenticated dispatch.
The staging runner supplies the fail-closed state machine and review gates.
"""
import base64
import json
import os
import time
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_BYTES = 131072


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def b64(raw):
    return base64.b64encode(raw).decode('ascii')


def unb64(value, size=None):
    if not isinstance(value, str) or len(value) > 2 * MAX_BYTES:
        raise ValueError('Invalid staged envelope')
    raw = base64.b64decode(value, validate=True)
    if size is not None and len(raw) != size:
        raise ValueError('Invalid staged envelope')
    return raw


def public(key):
    return b64(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def derive(private, peer, context):
    shared = private.exchange(X25519PublicKey.from_public_bytes(unb64(peer, 32)))
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b'jinny-staged-input-v1:' + canonical(context)).derive(shared)


@dataclass
class Receiver:
    announcement: dict
    deadline: float
    _private: object = field(repr=False)
    _consumed: bool = field(default=False, repr=False)

    def close(self):
        # Releasing Python references is not a guarantee of secure memory erasure.
        self._consumed = True
        self._private = None

    def receive(self, envelope_bytes):
        private = self._private
        available = not self._consumed and time.monotonic() <= self.deadline
        self.close()  # Even invalid input consumes the session. No automatic retries.
        try:
            if not available or not isinstance(envelope_bytes, bytes) or len(envelope_bytes) > 2 * MAX_BYTES:
                raise ValueError()
            envelope = json.loads(envelope_bytes)
            if not isinstance(envelope, dict) or set(envelope) != {'session', 'sender_key', 'nonce', 'ciphertext', 'signature'}:
                raise ValueError()
            if envelope['session'] != self.announcement:
                raise ValueError()
            signature = unb64(envelope.pop('signature'), 64)
            Ed25519PublicKey.from_public_bytes(unb64(self.announcement['signing_key'], 32)).verify(signature, canonical(envelope))
            aad = canonical({'session': self.announcement, 'sender_key': envelope['sender_key']})
            key = derive(private, envelope['sender_key'], self.announcement)
            plaintext = AESGCM(key).decrypt(unb64(envelope['nonce'], 12), unb64(envelope['ciphertext']), aad)
            if len(plaintext) > MAX_BYTES:
                raise ValueError()
            batch = json.loads(plaintext)
            if not isinstance(batch, dict) or set(batch) != {'jobs'} or not isinstance(batch['jobs'], list):
                raise ValueError()
            return batch
        except Exception:
            # Never relay ciphertext, plaintext, parser errors or provider strings.
            raise ValueError('Staged input rejected; session closed') from None


def create_receiver(repository, run_id, run_attempt, day, signing_key, ttl=600):
    if not all(isinstance(x, str) and x for x in (repository, run_id, run_attempt, day)):
        raise ValueError('Invalid staging context')
    if not 1 <= ttl <= 900:
        raise ValueError('Invalid staging lifetime')
    Ed25519PublicKey.from_public_bytes(unb64(signing_key, 32))
    key = X25519PrivateKey.generate()
    announcement = {'version': 1, 'repository': repository, 'run_id': run_id,
                    'run_attempt': run_attempt, 'day': day,
                    'session_id': b64(os.urandom(32)), 'receiver_key': public(key), 'signing_key': signing_key}
    return Receiver(announcement, time.monotonic() + ttl, key)


def seal_batch(announcement, batch, signing_private):
    if not isinstance(batch, dict) or set(batch) != {'jobs'} or not isinstance(batch['jobs'], list):
        raise ValueError('Invalid staged batch')
    if public(signing_private) != announcement['signing_key']:
        raise ValueError('Wrong staged signing identity')
    plaintext = canonical(batch)
    if len(plaintext) > MAX_BYTES:
        raise ValueError('Staged batch too large')
    sender = X25519PrivateKey.generate()
    sender_key = public(sender)
    aad = canonical({'session': announcement, 'sender_key': sender_key})
    key = derive(sender, announcement['receiver_key'], announcement)
    nonce = os.urandom(12)
    envelope = {'session': announcement, 'sender_key': sender_key, 'nonce': b64(nonce),
                'ciphertext': b64(AESGCM(key).encrypt(nonce, plaintext, aad))}
    envelope['signature'] = b64(signing_private.sign(canonical(envelope)))
    return canonical(envelope)
