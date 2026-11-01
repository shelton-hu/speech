"""Version safety checks: a release must never overwrite or downgrade a remote version."""
import sys
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from release import choose_version, deployment_files
from registry_upload import verified_manifest


class ReleaseTests(unittest.TestCase):
    def test_first_release_uses_current_source_version(self):
        self.assertEqual(choose_version('repo/speech', '0.2.0', inspect=lambda _: None), '0.2.0')

    def test_auto_skips_partial_and_complete_releases(self):
        remote = {'repo/speech:latest': {'annotations': {'org.opencontainers.image.version': '0.2.0'}},
                  'repo/speech:0.2.1-amd64': {'schemaVersion': 2},
                  'repo/speech:0.2.2': {'schemaVersion': 2}}
        self.assertEqual(choose_version('repo/speech', '0.2.0', inspect=remote.get), '0.2.3')

    def test_explicit_version_cannot_overwrite_platform_tag(self):
        with self.assertRaisesRegex(RuntimeError, '禁止覆盖'):
            choose_version('repo/speech', '0.2.0', '0.2.0', inspect=lambda ref: {} if ref.endswith('-amd64') else None)

    def test_latest_cannot_be_downgraded(self):
        remote = {'repo/speech:latest': {'annotations': {'org.opencontainers.image.version': '0.3.0'}}}
        with self.assertRaisesRegex(RuntimeError, '高于'):
            choose_version('repo/speech', '0.2.0', '0.2.1', inspect=remote.get)

    def test_unknown_latest_is_not_treated_as_missing(self):
        with self.assertRaisesRegex(RuntimeError, '缺少版本信息'):
            choose_version('repo/speech', '0.2.0', inspect=lambda ref: {'schemaVersion': 2} if ref.endswith(':latest') else None)

    def test_registry_error_does_not_fall_back_to_unchecked_version(self):
        def unavailable(_):
            raise RuntimeError('authorization failed')
        with self.assertRaisesRegex(RuntimeError, 'authorization failed'):
            choose_version('repo/speech', '0.2.0', inspect=unavailable)

    def test_invalid_version_rejected_before_build(self):
        with self.assertRaises(ValueError):
            choose_version('repo/speech', '0.2.0', '0.2.0; echo bad', inspect=lambda _: None)


class PublicationImageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.layout = Path(self.directory.name)
        self.image_id = 'sha256:' + 'a' * 64
        body = json.dumps({'config': {'digest': self.image_id}, 'layers': []}).encode()
        self.digest = 'sha256:' + hashlib.sha256(body).hexdigest()
        self.blob = self.layout / 'blobs' / 'sha256' / self.digest.split(':')[1]
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(body)
        (self.layout / 'index.json').write_text(json.dumps({'manifests': [{'digest': self.digest}]}))

    def test_export_must_match_deployment_verified_image(self):
        self.assertEqual(verified_manifest(self.layout, self.image_id), self.digest)
        with self.assertRaisesRegex(RuntimeError, '镜像不一致'):
            verified_manifest(self.layout, 'sha256:' + 'b' * 64)

    def test_modified_export_is_rejected_before_upload(self):
        self.blob.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, '摘要不匹配'):
            verified_manifest(self.layout, self.image_id)

    def test_extra_unverified_platform_is_rejected(self):
        (self.layout / 'index.json').write_text(json.dumps({'manifests': [{'digest': self.digest}] * 2}))
        with self.assertRaisesRegex(RuntimeError, '一个'):
            verified_manifest(self.layout, self.image_id)


class DeploymentVersionTests(unittest.TestCase):
    def test_new_release_deploys_its_own_version_even_with_old_source_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compose = ('name: speech\nservices:\n  assistant:\n'
                       '    image: ${SPEECH_IMAGE:-registry.example/speech:0.2.0}\n'
                       '    environment:\n      APP_TOKEN: ${APP_TOKEN:?required}\n')
            (root / 'compose.yaml').write_text(compose)
            (root / '.env.example').write_text('APP_TOKEN=\nLLM_API_KEY=\n')
            files = deployment_files(root, '0.2.1', 'registry.example/new-speech')
            result = (files / 'compose.yaml').read_text()
            self.assertIn('${SPEECH_IMAGE:-registry.example/new-speech:0.2.1}', result)
            self.assertNotIn(':0.2.0', result)
            self.assertIn('${APP_TOKEN:?required}', result)
            self.assertEqual((root / 'compose.yaml').read_text(), compose)
            self.assertEqual((files / '.env').read_text(), 'APP_TOKEN=\nLLM_API_KEY=\n')


if __name__ == '__main__':
    unittest.main()
