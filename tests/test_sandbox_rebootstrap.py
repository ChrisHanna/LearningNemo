import importlib.util
import base64
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
import pytest


SPEC = importlib.util.spec_from_file_location('rebootstrap', Path(__file__).parents[1] / 'scripts/repair-sandbox-bootstrap.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture_token(key, sandbox_id):
    header = {'typ': 'JWT', 'alg': 'EdDSA', 'kid': 'fixture'}
    claims = {'sub': 'spiffe://openshell/sandbox/' + sandbox_id, 'iss': 'openshell-gateway:learningnemo', 'aud': 'openshell-gateway:learningnemo', 'iat': 1000, 'exp': 1900, 'sandbox_id': sandbox_id}
    content = '.'.join(MODULE.encode_part(json.dumps(value).encode()) for value in (header, claims))
    return content + '.' + MODULE.encode_part(key.sign(content.encode()))


def test_authorized_rebootstrap_preserves_identity_and_short_lifetime():
    key, sandbox_id = Ed25519PrivateKey.generate(), str(uuid4())
    token = fixture_token(key, sandbox_id)
    renewed = MODULE.renew_signed_bootstrap(token, sandbox_id, 'learningnemo', 'fixture', key, key.public_key(), 5000)
    claims = json.loads(MODULE.decode_part(renewed.split('.')[1]))
    assert claims['sandbox_id'] == sandbox_id
    assert claims['iat'] == 5000 and claims['exp'] == 5900
    assert renewed != token


@pytest.mark.parametrize('mismatch', ['sandbox', 'issuer', 'kid', 'key'])
def test_rebootstrap_rejects_mismatched_authority(mismatch):
    key, sandbox_id = Ed25519PrivateKey.generate(), str(uuid4())
    token = fixture_token(key, sandbox_id)
    with pytest.raises(Exception):
        MODULE.renew_signed_bootstrap(token, str(uuid4()) if mismatch == 'sandbox' else sandbox_id, 'other' if mismatch == 'issuer' else 'learningnemo', 'other' if mismatch == 'kid' else 'fixture', key, Ed25519PrivateKey.generate().public_key() if mismatch == 'key' else key.public_key(), 5000)


def test_protobuf_unknown_configuration_fields_survive_rotation():
    message = MODULE.persisted_type()(id=str(uuid4()), name='planning-demo')
    message.spec.sandbox_token = 'old'
    unknown = b'\x9a\x06\x03abc'
    serialized = message.SerializeToString() + unknown
    restored = MODULE.persisted_type().FromString(serialized)
    restored.spec.sandbox_token = 'new'
    assert unknown in restored.SerializeToString()


@pytest.mark.parametrize('version', [0, 1])
def test_gateway_key_encodings_match_original_signer(version):
    key = Ed25519PrivateKey.generate()
    if version == 0:
        content = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    else:
        encoded = bytes.fromhex('3051020101300506032b657004220420') + key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()) + bytes.fromhex('812100') + key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        content = b'-----BEGIN PRIVATE KEY-----\n' + base64.b64encode(encoded) + b'\n-----END PRIVATE KEY-----\n'
    restored = MODULE.load_gateway_private_key(content)
    key.public_key().verify(restored.sign(b'repair-fixture'), b'repair-fixture')


def test_gateway_v2_key_rejects_mismatched_embedded_public_key():
    key = Ed25519PrivateKey.generate()
    encoded = bytes.fromhex('3051020101300506032b657004220420') + key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()) + bytes.fromhex('812100') + Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    content = b'-----BEGIN PRIVATE KEY-----\n' + base64.b64encode(encoded) + b'\n-----END PRIVATE KEY-----\n'
    with pytest.raises(ValueError):
        MODULE.load_gateway_private_key(content)


@pytest.mark.parametrize('invalid_phase', [None, 2, 4])
def test_gateway_reconciliation_is_atomic_and_preserves_configuration(tmp_path, invalid_phase):
    database, backup, root = tmp_path / 'gateway.db', tmp_path / 'backup', tmp_path / 'sandboxes'
    backup.mkdir()
    root.mkdir()
    inventory = []
    originals = []
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE objects (id TEXT PRIMARY KEY,name TEXT,workspace TEXT,resource_version INTEGER,payload BLOB,object_type TEXT,updated_at_ms INTEGER)')
        for index, name in enumerate(('planning-demo', 'execution-demo', 'probe-demo')):
            sandbox_id = str(uuid4())
            inventory.append({'id': sandbox_id, 'name': name})
            directory = root / sandbox_id
            directory.mkdir()
            (directory / 'stopped').write_text('stopped\n')
            message = MODULE.gateway_sandbox_type()(spec=b'unchanged-policy-and-image')
            message.metadata.id, message.metadata.name, message.metadata.workspace = sandbox_id, name, 'default'
            message.status.phase = invalid_phase if index == 2 and invalid_phase is not None else 3
            message.status.exit_code = 1
            message.status.current_policy_version = 8
            payload = message.SerializeToString() + b'\x9a\x06\x03abc'
            connection.execute('INSERT INTO objects VALUES (?,?,?,?,?,?,?)', (sandbox_id, name, 'default', 11, payload, 'sandbox', 0))
            originals.append(payload)
    if invalid_phase is not None:
        with pytest.raises(ValueError):
            MODULE.reconcile_stopped_gateway(database, backup, inventory, root)
        with sqlite3.connect(database) as connection:
            assert [row[0] for row in connection.execute('SELECT payload FROM objects')] == originals
    else:
        MODULE.reconcile_stopped_gateway(database, backup, inventory, root)
        with sqlite3.connect(database) as connection:
            for payload, version in connection.execute('SELECT payload,resource_version FROM objects'):
                message = MODULE.gateway_sandbox_type().FromString(payload)
                assert message.status.phase == 7 and not message.status.HasField('exit_code')
                assert message.status.current_policy_version == 8 and message.spec == b'unchanged-policy-and-image'
                assert version == message.metadata.resource_version == 12
                assert b'\x9a\x06\x03abc' in payload
    with sqlite3.connect(backup / 'gateway-before.db') as connection:
        assert [row[0] for row in connection.execute('SELECT payload FROM objects')] == originals