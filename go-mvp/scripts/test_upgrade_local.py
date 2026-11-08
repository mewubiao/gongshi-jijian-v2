"""Rollback must not lose reports accepted just before application shutdown."""
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('upgrade_local', Path(__file__).with_name('upgrade-local.py'))
upgrade = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(upgrade)


class RollbackProtection(unittest.TestCase):
    def setUp(self):
        artifacts = Path(__file__).resolve().parents[1] / 'artifacts'
        artifacts.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temp.cleanup)
        self.backup = Path(self.temp.name)
        (self.backup / 'workshop.previous').write_bytes(b'old executable fixture')
        root = self.backup / 'installed'
        (root / 'run').mkdir(parents=True)
        root_patch = patch.object(upgrade, 'ROOT', root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_report_committed_between_precheck_and_shutdown_keeps_current_app(self):
        with patch.object(upgrade, 'snapshot', side_effect=[{'batches': []}, {'batches': [{'algorithm': 'interval-v2'}]}]), \
                patch.object(upgrade, 'stop_app') as stop, \
                patch.object(upgrade, 'start_app', return_value='WB5-interval-v2-r1') as start, \
                patch.object(upgrade, 'atomic_binary') as replace, \
                patch.object(upgrade, 'write_json') as manifest:
            with self.assertRaisesRegex(RuntimeError, 'v2 reports'):
                upgrade.restore(self.backup)
            stop.assert_called_once()
            start.assert_called_once()
            replace.assert_not_called()
            manifest.assert_not_called()

    def test_existing_v2_report_refuses_without_interrupting_service(self):
        with patch.object(upgrade, 'snapshot', return_value={'batches': [{'algorithm': 'interval-v2'}]}), \
                patch.object(upgrade, 'stop_app') as stop, \
                patch.object(upgrade, 'atomic_binary') as replace:
            with self.assertRaisesRegex(RuntimeError, 'v2 reports'):
                upgrade.restore(self.backup)
            stop.assert_not_called()
            replace.assert_not_called()

    def test_missing_backup_does_not_stop_service(self):
        with patch.object(upgrade, 'snapshot', return_value={'batches': []}), \
                patch.object(upgrade, 'stop_app') as stop:
            with self.assertRaises(FileNotFoundError):
                upgrade.restore(self.backup / 'missing')
            stop.assert_not_called()

    def test_database_read_failure_after_shutdown_restarts_current_app(self):
        with patch.object(upgrade, 'snapshot', side_effect=[{'batches': []}, OSError('database unavailable')]), \
                patch.object(upgrade, 'stop_app'), \
                patch.object(upgrade, 'start_app', return_value='WB5-interval-v2-r1') as start, \
                patch.object(upgrade, 'atomic_binary') as replace:
            with self.assertRaises(OSError):
                upgrade.restore(self.backup)
            start.assert_called_once()
            replace.assert_not_called()

    def test_stop_waits_for_the_verified_service_to_exit(self):
        job = SimpleNamespace(returncode=0, stdout='\tpid = 12345\n')
        with patch.dict(upgrade.os.environ, {'MULTICA_CLI_PATH': '/test/multica'}), \
                patch.object(upgrade.subprocess, 'check_output', side_effect=[b'{"pid":99}', str(upgrade.BINARY) + ' -demo']), \
                patch.object(upgrade.subprocess, 'run', side_effect=[job, None]) as run:
            upgrade.stop_app()
            self.assertEqual(run.call_args.args[0], ['/bin/launchctl', 'bootout', '--wait', upgrade.DOMAIN + '/' + upgrade.LABEL])
            self.assertEqual(run.call_args.kwargs['timeout'], 35)

    def test_daemon_pid_is_never_stopped(self):
        job = SimpleNamespace(returncode=0, stdout='\tpid = 12345\n')
        with patch.dict(upgrade.os.environ, {'MULTICA_CLI_PATH': '/test/multica'}), \
                patch.object(upgrade.subprocess, 'check_output', side_effect=[b'{"pid":12345}', str(upgrade.BINARY) + ' -demo']), \
                patch.object(upgrade.subprocess, 'run', return_value=job) as run:
            with self.assertRaisesRegex(RuntimeError, 'Unexpected app PID'):
                upgrade.stop_app()
            self.assertEqual(run.call_count, 1)  # Only read the launchd job; no stop.


if __name__ == '__main__':
    unittest.main()
