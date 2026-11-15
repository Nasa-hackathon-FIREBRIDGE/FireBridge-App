"""A pending site must stay blank unless a verified new App release succeeds."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2] / 'deploy/webapp_gateway.py'
        spec = importlib.util.spec_from_file_location('publication_gateway', source)
        self.gateway = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.gateway)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.gateway.ROOT = self.root
        self.revision = 'a' * 40
        (self.root / '.env').write_text('API_IMAGE=old\n')
        (self.root / 'nginx.proxy.conf').write_text('blank website')
        (self.root / 'nginx.release.conf').write_text('new application routes')
        (self.root / 'pending-new-app.json').write_text(json.dumps({'repository': self.gateway.REPO}))

    def activate(self, run=None, legacy=False):
        with patch.object(self.gateway, 'verify') as verify, patch.object(self.gateway, 'run', side_effect=run) as command, patch.object(self.gateway.subprocess, 'run'):
            self.gateway.activate(self.revision, allow_legacy=legacy)
        verify.assert_called_once_with(self.revision, allow_legacy=legacy)
        return command

    def test_verified_new_release_publishes_after_health_checks(self):
        command = self.activate()
        self.assertEqual((self.root / 'nginx.proxy.conf').read_text(), 'new application routes')
        self.assertFalse((self.root / 'pending-new-app.json').exists())
        receipt = json.loads((self.root / 'deployment.json').read_text())
        self.assertEqual(receipt['publication_state'], 'published')
        calls = [entry.args for entry in command.call_args_list]
        health = next(i for i, args in enumerate(calls) if 'api' in args and 'node' in args)
        reload = next(i for i, args in enumerate(calls) if args[-2:] == ('-s', 'reload'))
        self.assertLess(health, reload)

    def test_failed_nginx_validation_restores_blank_site_and_old_images(self):
        attempts = 0

        def run(*args, **kwargs):
            nonlocal attempts
            if args[-1] == '-t':
                attempts += 1
                if attempts == 1:
                    raise RuntimeError('invalid release proxy')
            return ''

        with self.assertRaisesRegex(RuntimeError, 'invalid release proxy'):
            self.activate(run)
        self.assertEqual((self.root / 'nginx.proxy.conf').read_text(), 'blank website')
        self.assertEqual((self.root / '.env').read_text(), 'API_IMAGE=old\n')
        self.assertTrue((self.root / 'pending-new-app.json').exists())

    def test_legacy_rollback_cannot_publish_pending_website(self):
        command = self.activate(legacy=True)
        self.assertEqual((self.root / 'nginx.proxy.conf').read_text(), 'blank website')
        self.assertTrue((self.root / 'pending-new-app.json').exists())
        self.assertFalse(any('nginx' in call.args for call in command.call_args_list))
        self.assertEqual(json.loads((self.root / 'deployment.json').read_text())['publication_state'], 'blank_pending_new_repository_release')

    def test_wrong_repository_marker_is_rejected_before_activation(self):
        (self.root / 'pending-new-app.json').write_text(json.dumps({'repository': 'https://github.com/example/unrelated'}))
        with self.assertRaisesRegex(ValueError, 'different repository'):
            self.activate()
        self.assertEqual((self.root / '.env').read_text(), 'API_IMAGE=old\n')


if __name__ == '__main__':
    unittest.main()
