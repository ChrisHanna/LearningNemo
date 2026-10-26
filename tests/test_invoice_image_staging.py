import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def module(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'infra/next-phase'))
    import stage_invoice_image
    return stage_invoice_image


def test_staging_seals_credential_to_host_key_and_attempt(monkeypatch):
    staging = module(monkeypatch)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    run_id, secret = 'a' * 32, 'fixture-private-credential'
    envelope = staging.seal(public, secret, run_id)
    key = private.decrypt(base64.b64decode(envelope['key']), padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    assert AESGCM(key).decrypt(base64.b64decode(envelope['nonce']), base64.b64decode(envelope['ciphertext']), run_id.encode()).decode() == secret
    script = staging.pull_script(run_id, staging.REGISTRY + '/' + staging.REPOSITORY + '@sha256:' + 'b' * 64, envelope)
    assert secret not in script
    assert 'podman/podman.sock' in script and 'setuid(user.pw_uid)' in script
    assert 'RepoDigests' in script and 'OPENSHELL_REGISTRY_TOKEN' not in script


def test_staging_rejects_unpinned_or_external_images(monkeypatch):
    staging = module(monkeypatch)
    for image in ('example.com/image:latest', staging.REGISTRY + '/' + staging.REPOSITORY + ':latest'):
        with pytest.raises(ValueError):
            staging.pull_script('a' * 32, image, {})


def test_release_requires_exact_rootless_staged_digest(monkeypatch, tmp_path):
    import json
    staging = module(monkeypatch)
    image = staging.REGISTRY + '/' + staging.REPOSITORY + '@sha256:' + 'b' * 64
    path = tmp_path/'invoice-image-staged.verified.json'
    with pytest.raises(ValueError,match='Stage the pinned'):
        staging.require_staged_image(tmp_path,image)
    path.write_text(json.dumps({'image':image,'rootless_uid':1000}))
    assert staging.require_staged_image(tmp_path,image)['image']==image
    with pytest.raises(ValueError,match='differs'):
        staging.require_staged_image(tmp_path,image.replace('b'*64,'c'*64))
    path.write_text(json.dumps({'image':image,'rootless_uid':0}))
    with pytest.raises(ValueError,match='differs'):
        staging.require_staged_image(tmp_path,image)