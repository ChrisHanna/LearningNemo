"""Host-admin-only rebootstrap for retained OpenShell 0.0.116 VM workspaces."""

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from uuid import UUID

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key, load_pem_public_key
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory


def persisted_type():
    schema = descriptor_pb2.FileDescriptorProto(name='retained_sandbox.proto', package='retained', syntax='proto3')
    spec = schema.message_type.add(name='Spec')
    spec.field.add(name='sandbox_token', number=11, label=1, type=9)
    sandbox = schema.message_type.add(name='Sandbox')
    sandbox.field.add(name='id', number=1, label=1, type=9)
    sandbox.field.add(name='name', number=2, label=1, type=9)
    sandbox.field.add(name='spec', number=4, label=1, type=11, type_name='.retained.Spec')
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    descriptor = pool.FindMessageTypeByName('retained.Sandbox')
    return message_factory.GetMessageClass(descriptor) if hasattr(message_factory, 'GetMessageClass') else message_factory.MessageFactory(pool).GetPrototype(descriptor)


def decode_part(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def encode_part(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def load_gateway_private_key(content):
    try:
        return load_pem_private_key(content, password=None)
    except ValueError:
        lines = content.splitlines()
        if lines[0] != b'-----BEGIN PRIVATE KEY-----' or lines[-1] != b'-----END PRIVATE KEY-----':
            raise ValueError('unexpected gateway private key label') from None
        encoded = base64.b64decode(b''.join(lines[1:-1]), validate=True)
        prefix = bytes.fromhex('3051020101300506032b657004220420')
        if len(encoded) != 83 or not encoded.startswith(prefix) or encoded[48:51] != bytes.fromhex('812100'):
            raise ValueError('expected canonical Ed25519 OneAsymmetricKey encoding') from None
        key = Ed25519PrivateKey.from_private_bytes(encoded[16:48])
        if key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw) != encoded[51:]:
            raise ValueError('embedded gateway public key differs') from None
        return key


def renew_signed_bootstrap(token, sandbox_id, gateway_id, kid, private_key, public_key, now):
    UUID(sandbox_id)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError('expected Ed25519 gateway key')
    header_part, claims_part, signature_part = token.strip().split('.')
    public_key.verify(decode_part(signature_part), (header_part + '.' + claims_part).encode())
    header, claims = json.loads(decode_part(header_part)), json.loads(decode_part(claims_part))
    identity = 'openshell-gateway:' + gateway_id
    if (header != {'typ': 'JWT', 'alg': 'EdDSA', 'kid': kid}
            or set(claims) != {'sub', 'iss', 'aud', 'iat', 'exp', 'sandbox_id'}
            or claims['sandbox_id'] != sandbox_id or claims['sub'] != 'spiffe://openshell/sandbox/' + sandbox_id
            or claims['iss'] != identity or claims['aud'] != identity
            or type(claims['iat']) is not int or type(claims['exp']) is not int
            or claims['exp'] - claims['iat'] != 900 or claims['iat'] > now):
        raise ValueError('persisted bootstrap identity or lifetime differs')
    renewed = {**claims, 'iat': now, 'exp': now + 900}
    signing_input = header_part + '.' + encode_part(json.dumps(renewed, separators=(',', ':')).encode())
    signature = private_key.sign(signing_input.encode())
    public_key.verify(signature, signing_input.encode())
    return signing_input + '.' + encode_part(signature)


def private_write(path, content, uid=0, gid=0):
    temporary = path.with_name(path.name + '.recovery-new')
    with open(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.chown(temporary, uid, gid)
    os.replace(temporary, path)


def gateway_sandbox_type():
    schema = descriptor_pb2.FileDescriptorProto(name='gateway_recovery.proto', package='recovery', syntax='proto3')
    metadata = schema.message_type.add(name='Metadata')
    for name, number, field_type in (('id', 1, 9), ('name', 2, 9), ('resource_version', 5, 4), ('workspace', 7, 9), ('deletion_timestamp_ms', 8, 3)):
        metadata.field.add(name=name, number=number, label=1, type=field_type)
    condition = schema.message_type.add(name='Condition')
    for number, name in enumerate(('type', 'status', 'reason', 'message', 'last_transition_time'), 1):
        condition.field.add(name=name, number=number, label=1, type=9)
    status = schema.message_type.add(name='Status')
    for number, name in enumerate(('sandbox_name', 'agent_pod', 'agent_fd', 'sandbox_fd'), 1):
        status.field.add(name=name, number=number, label=1, type=9)
    status.field.add(name='conditions', number=5, label=3, type=11, type_name='.recovery.Condition')
    status.field.add(name='phase', number=6, label=1, type=5)
    status.field.add(name='current_policy_version', number=7, label=1, type=13)
    status.field.add(name='main_process_instance_id', number=8, label=1, type=9)
    status.oneof_decl.add(name='_exit_code')
    status.field.add(name='exit_code', number=9, label=1, type=5, proto3_optional=True, oneof_index=0)
    sandbox = schema.message_type.add(name='Sandbox')
    sandbox.field.add(name='metadata', number=1, label=1, type=11, type_name='.recovery.Metadata')
    sandbox.field.add(name='spec', number=2, label=1, type=12)
    sandbox.field.add(name='status', number=3, label=1, type=11, type_name='.recovery.Status')
    pool = descriptor_pool.DescriptorPool()
    pool.Add(schema)
    return message_factory.GetMessageClass(pool.FindMessageTypeByName('recovery.Sandbox'))


def reconcile_stopped_gateway(database, backup, inventory, root):
    expected = {item['id']: item['name'] for item in inventory}
    if len(expected) != 3 or set(expected.values()) != {'planning-demo', 'execution-demo', 'probe-demo'}:
        raise ValueError('unexpected lifecycle inventory')
    if database.is_symlink() or not database.is_file():
        raise ValueError('invalid gateway database')
    backup_file = backup / 'gateway-before.db'
    if backup_file.exists():
        raise ValueError('gateway backup already exists')
    private_write(backup_file, b'', os.geteuid(), os.getegid())
    with sqlite3.connect(database.as_uri() + '?mode=rw', uri=True) as connection:
        with sqlite3.connect(backup_file) as destination:
            connection.backup(destination)
        connection.execute('BEGIN IMMEDIATE')
        rows = connection.execute("SELECT id,name,workspace,resource_version,payload FROM objects WHERE object_type='sandbox'").fetchall()
        if {row[0]: row[1] for row in rows} != expected:
            raise ValueError('gateway sandbox inventory differs')
        changes = []
        for sandbox_id, name, workspace, version, payload in rows:
            message = gateway_sandbox_type().FromString(payload)
            metadata = message.metadata
            directory = root / sandbox_id
            if (metadata.id != sandbox_id or metadata.name != name or workspace != 'default'
                    or metadata.workspace not in ('', 'default') or metadata.deletion_timestamp_ms
                    or message.status.phase not in (3, 7)
                    or not (directory / 'stopped').is_file() or (directory / 'main-process-exited').exists()):
                raise ValueError('gateway lifecycle is not a verified stopped repair candidate')
            spec_before = message.spec
            message.status.phase = 7
            for field in ('agent_pod', 'agent_fd', 'sandbox_fd', 'main_process_instance_id', 'exit_code', 'conditions'):
                message.status.ClearField(field)
            message.status.conditions.add(type='Ready', status='False', reason='Stopped', message='Host-admin retained recovery; normal startup required')
            metadata.resource_version = version + 1
            renewed = message.SerializeToString()
            if gateway_sandbox_type().FromString(renewed).spec != spec_before:
                raise ValueError('sandbox policy or configuration changed')
            changes.append((renewed, version + 1, int(time.time() * 1000), sandbox_id, version))
        for change in changes:
            if connection.execute('UPDATE objects SET payload=?,resource_version=?,updated_at_ms=? WHERE id=? AND resource_version=?', change).rowcount != 1:
                raise ValueError('gateway lifecycle compare-and-swap failed')
        if connection.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('gateway database integrity check failed')
    print(json.dumps({'gatewayLifecycleReconciled': 3, 'phase': 'Stopped', 'readyAssumed': False, 'databaseBackupSha256': hashlib.sha256(backup_file.read_bytes()).hexdigest()}), flush=True)


def main():
    if os.geteuid() != 0:
        raise ValueError('host administrator required')
    backup = Path(sys.argv[1])
    if backup.parent != Path('/var/lib/learningnemo-saw') or not backup.name.startswith('rebootstrap-'):
        raise ValueError('unexpected repair directory')
    inventory = json.loads((backup / 'inventory.json').read_text())
    if len(inventory) != 3 or {item['name'] for item in inventory} != {'planning-demo', 'execution-demo', 'probe-demo'}:
        raise ValueError('expected exactly three owned demo workspaces')
    home = Path('/home/sawadmin')
    configuration = tomllib.loads((home / '.config/openshell/gateway.toml').read_text())
    gateway = configuration['openshell']['gateway']
    jwt = gateway['gateway_jwt']
    if gateway['bind_address'] != '127.0.0.1:17670' or gateway['compute_drivers'] != ['vm'] or jwt['ttl_secs'] != 900 or gateway['auth']['allow_unauthenticated_users'] or not gateway['mtls_auth']['enabled']:
        raise ValueError('gateway authentication contract differs')
    private_key = load_gateway_private_key(Path(jwt['signing_key_path']).read_bytes())
    public_key = load_pem_public_key(Path(jwt['public_key_path']).read_bytes())
    kid = Path(jwt['kid_path']).read_text().strip()
    root = home / '.local/state/openshell/vm/sandboxes'
    records = []
    now = int(time.time())
    for item in inventory:
        sandbox_id = str(UUID(item['id']))
        directory = root / sandbox_id
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('invalid persisted workspace directory')
        request = directory / 'sandbox.pb'
        serialized = request.read_bytes()
        message = persisted_type().FromString(serialized)
        if message.id != sandbox_id or message.name != item['name']:
            raise ValueError('persisted workspace identity mismatch')
        message.spec.sandbox_token = renew_signed_bootstrap(message.spec.sandbox_token, sandbox_id, jwt['gateway_id'], kid, private_key, public_key, now)
        overlay = directory / 'overlay.ext4'
        for descriptor in Path('/proc').glob('[0-9]*/fd/*'):
            try:
                if descriptor.resolve() == overlay:
                    raise ValueError('workspace disk is still open')
            except (FileNotFoundError, PermissionError):
                continue
        records.append((item, directory, request, serialized, message.SerializeToString()))
    for item, directory, request, original, renewed in records:
        archive = backup / item['id']
        archive.mkdir(mode=0o700)
        private_write(archive / 'sandbox.pb', original)
        mount = archive / 'overlay'
        mount.mkdir(mode=0o700)
        subprocess.run(['mount', '-o', 'loop,rw', str(directory / 'overlay.ext4'), str(mount)], check=True)
        try:
            target = mount / 'upper/etc'
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise ValueError('unexpected resolver parent')
            target.mkdir(parents=True, exist_ok=True)
            resolver = target / 'resolv.conf'
            if resolver.is_symlink():
                raise ValueError('unexpected resolver symlink')
            if resolver.exists():
                shutil.copy2(resolver, archive / 'resolv.conf.before')
            resolver.write_text('nameserver 168.63.129.16\noptions timeout:2 attempts:2\n')
            resolver.chmod(0o644)
            os.chown(resolver, 0, 0)
            os.sync()
        finally:
            subprocess.run(['umount', str(mount)], check=True)
        metadata = request.stat()
        private_write(request, renewed, metadata.st_uid, metadata.st_gid)
        for marker in ('main-process-exited', 'stopped'):
            path = directory / marker
            if path.exists():
                shutil.copy2(path, archive / marker)
        tombstone = directory / 'main-process-exited'
        if tombstone.exists():
            tombstone.unlink()
        private_write(directory / 'stopped', b'stopped\n', metadata.st_uid, metadata.st_gid)
        print(json.dumps({'sandbox': item['name'], 'sandboxId': item['id'], 'bootstrapLifetimeSeconds': 900, 'resolver': '168.63.129.16', 'workspacePreserved': True, 'requestBeforeSha256': hashlib.sha256(original).hexdigest()}), flush=True)
    reconcile_stopped_gateway(home / '.local/state/openshell/gateway/openshell.db', backup, inventory, root)


if __name__ == '__main__':
    main()