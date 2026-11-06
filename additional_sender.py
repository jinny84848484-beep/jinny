"""Offline one-run sender utilities. Never dispatch, upload, purchase or call providers.

Keep the local private key file until encrypted recovery is downloaded and checked.
Do not commit it, attach it to public artifacts, or reuse it for another run.
"""
import argparse
import base64
import json
import os
import re
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
import additional_batch as a
import additional_transport as at
import staged_transfer as t


def _private_bytes(key):
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())


def write_private_file(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle: handle.write(raw)


def create_one_run_keys(path):
    signing = Ed25519PrivateKey.generate(); recovery = X25519PrivateKey.generate()
    value = {'signing_private': t.b64(_private_bytes(signing)), 'recovery_private': t.b64(_private_bytes(recovery))}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as handle: json.dump(value, handle)
    return {'staged_sender_public_key': t.public(signing), 'recovery_public_key': t.public(recovery)}


def seal_one_run(announcement, payload, key_file, *, repository, run_id, production_date, approval_hash):
    private = json.loads(Path(key_file).read_text())
    signing = Ed25519PrivateKey.from_private_bytes(t.unb64(private['signing_private'], 32))
    expected = {'version': 1, 'repository': repository, 'run_id': str(run_id), 'run_attempt': '1',
                'day': production_date, 'signing_key': t.public(signing)}
    a.require(all(announcement.get(k) == v for k, v in expected.items()), 'Unexpected receiver identity')
    verified = at.unwrap_payload(payload)
    a.require(a.digest(verified['approval']) == approval_hash and
              verified['approval'].get('packet_hash') == a.digest(verified['packet']) and
              verified['packet'].get('production_date') == production_date,
              'Sender input not bound to dispatch approval')
    return t.seal_batch(announcement, payload, signing)


def prepare_graphics_packet(job, graphics_spec, batch_id, output_dir, config, *, completed_anchor_day=None):
    """Offline preparation only. Does not create approval or assert paid budget."""
    import copy
    import original_graphics as graphics
    from additional_runtime import Runtime
    candidate = copy.deepcopy(job)
    day = batch_id[:10]
    a.require(re.fullmatch(re.escape(day) + '-extra-[0-9]{2}', batch_id) is not None,
              'Invalid additional batch identity')
    candidate['id'] = batch_id + '-' + candidate['topic_key']
    candidate['channel_id'] = config['channels']['ojirap']['channel_id']
    manifest = graphics.build_original_graphics(candidate, output_dir,
        approved_story_hash=a.digest(candidate), production_date=day, graphics_spec=graphics_spec)
    return {'production_date': day, 'completed_anchor_day': completed_anchor_day or day, 'batch_id': batch_id, 'job': candidate,
            'event_keys': [candidate['topic_key']], 'media_policy': 'original_graphics',
            'graphics_spec': copy.deepcopy(graphics_spec), 'graphics_manifest': manifest,
            'media': Runtime.graphics_media(candidate, batch_id, manifest)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    key = sub.add_parser('keys'); key.add_argument('private_key_file')
    seal = sub.add_parser('seal')
    for field in ('announcement', 'payload', 'private_key_file', 'run_id', 'production_date', 'approval_hash', 'output'):
        seal.add_argument(field)
    dec = sub.add_parser('decrypt')
    for field in ('encrypted_artifact', 'private_key_file', 'expected_context', 'output_zip'): dec.add_argument(field)
    args = parser.parse_args()
    if args.mode == 'keys':
        print(json.dumps(create_one_run_keys(args.private_key_file)))
    elif args.mode == 'seal':
        raw = seal_one_run(json.loads(Path(args.announcement).read_text()), json.loads(Path(args.payload).read_text()),
            args.private_key_file, repository='jinny84848484-beep/jinny', run_id=args.run_id,
            production_date=args.production_date, approval_hash=args.approval_hash)
        Path(args.output).write_bytes(raw)
        print(json.dumps({'ciphertext_sha256': a.hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}))
    else:
        private = json.loads(Path(args.private_key_file).read_text())
        key = X25519PrivateKey.from_private_bytes(t.unb64(private['recovery_private'], 32))
        raw = at.decrypt_recovery(Path(args.encrypted_artifact).read_bytes(), key,
                                  json.loads(Path(args.expected_context).read_text()))
        # Do not automatically extract ZIP paths from any archive.
        write_private_file(args.output_zip, raw)
        print(json.dumps({'archive_sha256': a.hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}))


if __name__ == '__main__':
    main()
