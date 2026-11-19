"""Real local journal/state-loss regressions; broker traffic is synthetic only."""
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from personal_trading.tradelocker.journal import Journal
from personal_trading.tradelocker.tl_candidate import ReadOnlyError

BINDING = 'a' * 64


class JournalRecoveryTests(unittest.TestCase):
    def test_existing_empty_directory_is_not_evidence_of_first_use(self):
        for established in (False, True):
            with self.subTest(established=established), \
                    tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
                path = Path(root) / 'state'
                if established:
                    with Journal(path, BINDING) as journal:
                        journal.append({'kind': 'INTENT', 'approval_id': 'synthetic-lost-both'})
                    # Adversarial loss only: production must never remove locks.
                    (path / 'journal.json').unlink()
                    (path / 'lock').unlink()
                else:
                    path.mkdir(mode=0o700)
                inode = path.stat().st_ino
                with self.assertRaises(ReadOnlyError):
                    with Journal(path, BINDING):
                        pass
                self.assertEqual(list(path.iterdir()), [])
                self.assertEqual(path.stat().st_ino, inode)

    def test_missing_established_lock_is_not_recreated(self):
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            path = Path(root) / 'state'
            event = {'kind': 'INTENT', 'approval_id': 'synthetic-keep-history'}
            with Journal(path, BINDING) as journal:
                journal.append(event)
            snapshot = (path / 'journal.json').read_bytes()
            inode = (path / 'lock').stat().st_ino
            (path / 'lock').rename(path / 'saved-lock')
            with self.assertRaises(ReadOnlyError):
                with Journal(path, BINDING):
                    pass
            self.assertFalse((path / 'lock').exists())
            self.assertEqual((path / 'journal.json').read_bytes(), snapshot)
            (path / 'saved-lock').rename(path / 'lock')
            with Journal(path, BINDING) as journal:
                self.assertTrue(journal.restarted)
                self.assertEqual(journal.events, [event])
            self.assertEqual((path / 'lock').stat().st_ino, inode)

    def test_new_directory_does_not_adopt_preexisting_lock(self):
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            path = Path(root) / 'state'
            real_mkdir = Path.mkdir
            inodes = []

            def mkdir(directory, *args, **kwargs):
                real_mkdir(directory, *args, **kwargs)
                fd = os.open(directory / 'lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    inodes.append(os.fstat(fd).st_ino)
                finally:
                    os.close(fd)

            with patch.object(Path, 'mkdir', mkdir), self.assertRaises(ReadOnlyError):
                with Journal(path, BINDING):
                    pass
            self.assertEqual((path / 'lock').stat().st_ino, inodes[0])
            self.assertFalse((path / 'journal.json').exists())

    def test_symlink_state_directory_is_refused_without_changing_target(self):
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            path = Path(root) / 'state'
            alias = Path(root) / 'alias'
            with Journal(path, BINDING) as journal:
                journal.append({'kind': 'INTENT', 'approval_id': 'synthetic-symlink'})
            snapshot = (path / 'journal.json').read_bytes()
            inode = (path / 'lock').stat().st_ino
            alias.symlink_to(path, target_is_directory=True)
            with self.assertRaises(ReadOnlyError):
                with Journal(alias, BINDING):
                    pass
            self.assertEqual((path / 'journal.json').read_bytes(), snapshot)
            self.assertEqual((path / 'lock').stat().st_ino, inode)
            with Journal(path, BINDING) as journal:
                self.assertTrue(journal.restarted)

    def test_initial_directory_and_lock_are_durable_before_snapshot_publish(self):
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            path = Path(root) / 'state'
            parent_stat = Path(root).stat()
            events = []
            real_fsync, real_replace = os.fsync, os.replace

            def fsync(fd):
                info = os.fstat(fd)
                identity = (info.st_dev, info.st_ino)
                if identity == (parent_stat.st_dev, parent_stat.st_ino):
                    name = 'parent'
                elif stat.S_ISDIR(info.st_mode):
                    name = 'state-directory'
                elif identity == ((path / 'lock').stat().st_dev,
                                  (path / 'lock').stat().st_ino):
                    name = 'lock'
                else:
                    name = 'snapshot'
                real_fsync(fd)
                events.append(name)

            def replace(*args, **kwargs):
                real_replace(*args, **kwargs)
                events.append('publish')

            with patch('personal_trading.tradelocker.journal.os.fsync', fsync), \
                    patch('personal_trading.tradelocker.journal.os.replace', replace):
                with Journal(path, BINDING) as journal:
                    self.assertFalse(journal.restarted)
            self.assertIn('parent', events, 'New state directory entry was not fsynced')
            self.assertIn('lock', events, 'Persistent lock inode was not fsynced')
            self.assertLess(events.index('parent'), events.index('publish'))
            self.assertLess(events.index('lock'), events.index('publish'))
            self.assertLess(events.index('snapshot'), events.index('publish'))
            self.assertEqual(events[-1], 'state-directory')

    def test_parent_fsync_failure_leaves_refusing_state_and_releases_lock(self):
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            path = Path(root) / 'state'
            parent_stat = Path(root).stat()
            real_fsync = os.fsync
            injected = []

            def fsync(fd):
                info = os.fstat(fd)
                if (info.st_dev, info.st_ino) == (parent_stat.st_dev, parent_stat.st_ino):
                    injected.append(True)
                    raise OSError('SYNTHETIC_PARENT_FSYNC_FAILURE')
                real_fsync(fd)

            with patch('personal_trading.tradelocker.journal.os.fsync', fsync):
                with self.assertRaises(ReadOnlyError) as caught:
                    with Journal(path, BINDING):
                        pass
            self.assertEqual(injected, [True])
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn('SYNTHETIC_PARENT_FSYNC_FAILURE', str(caught.exception))
            self.assertFalse((path / 'journal.json').exists())
            inode = (path / 'lock').stat().st_ino
            fd = os.open(path / 'lock', os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            with self.assertRaises(ReadOnlyError):
                with Journal(path, BINDING):
                    pass
            self.assertEqual((path / 'lock').stat().st_ino, inode)
            self.assertFalse((path / 'journal.json').exists())

    def test_initialization_fsync_failures_cannot_be_retried_as_fresh(self):
        for stage in ('lock', 'directory-before-publish', 'snapshot', 'directory-after-publish'):
            with self.subTest(stage=stage), \
                    tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
                path = Path(root) / 'state'
                parent_stat = Path(root).stat()
                real_fsync = os.fsync
                injected = []

                def fsync(fd):
                    info = os.fstat(fd)
                    identity = (info.st_dev, info.st_ino)
                    if identity == (parent_stat.st_dev, parent_stat.st_ino):
                        name = 'parent'
                    elif stat.S_ISDIR(info.st_mode):
                        name = ('directory-after-publish' if (path / 'journal.json').exists()
                                else 'directory-before-publish')
                    elif info.st_ino == (path / 'lock').stat().st_ino:
                        name = 'lock'
                    else:
                        name = 'snapshot'
                    if name == stage:
                        injected.append(name)
                        raise OSError('SYNTHETIC_INIT_FSYNC_FAILURE')
                    real_fsync(fd)

                with patch('personal_trading.tradelocker.journal.os.fsync', fsync):
                    with self.assertRaises(ReadOnlyError):
                        with Journal(path, BINDING):
                            pass
                self.assertEqual(injected, [stage])
                inode = (path / 'lock').stat().st_ino
                fd = os.open(path / 'lock', os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(fd)
                if (path / 'journal.json').exists():
                    with Journal(path, BINDING) as journal:
                        self.assertTrue(journal.restarted)
                else:
                    with self.assertRaises(ReadOnlyError):
                        with Journal(path, BINDING):
                            pass
                self.assertEqual((path / 'lock').stat().st_ino, inode)
                self.assertEqual(list(path.glob('.journal-*')), [])

    def test_process_death_during_initialization_never_resets_history(self):
        code = '''
import os, sys
from pathlib import Path
from personal_trading.tradelocker.journal import Journal
path, phase = Path(sys.argv[1]), sys.argv[2]
parent = path.parent.stat()
real_fsync = os.fsync
def fsync(fd):
    info = os.fstat(fd)
    real_fsync(fd)
    if phase == 'parent' and (info.st_dev, info.st_ino) == (parent.st_dev, parent.st_ino):
        os._exit(71)
    if phase == 'snapshot' and (path / 'journal.json').exists():
        os._exit(71)
os.fsync = fsync
with Journal(path, 'a' * 64) as journal:
    journal.append({'kind': 'INTENT', 'approval_id': 'synthetic-crashed-once'})
    journal.append({'kind': 'RATE', 'timestamp': 1234567890})
    os._exit(71)
'''
        for phase in ('parent', 'snapshot', 'intent'):
            with self.subTest(phase=phase), \
                    tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
                path = Path(root) / 'state'
                child = subprocess.run(
                    [sys.executable, '-B', '-W', 'error', '-c', code, str(path), phase],
                    cwd=Path(__file__).resolve().parents[2],
                    env={'PATH': '/usr/bin:/bin', 'TMPDIR': tempfile.gettempdir()},
                    capture_output=True, text=True, timeout=5,
                )
                self.assertEqual(child.returncode, 71, child.stderr)
                inode = (path / 'lock').stat().st_ino
                if phase == 'parent':
                    with self.assertRaises(ReadOnlyError):
                        with Journal(path, BINDING):
                            pass
                    self.assertFalse((path / 'journal.json').exists())
                else:
                    with Journal(path, BINDING) as journal:
                        self.assertTrue(journal.restarted)
                        if phase == 'intent':
                            self.assertEqual(journal.events, [
                                {'kind': 'INTENT', 'approval_id': 'synthetic-crashed-once'},
                                {'kind': 'RATE', 'timestamp': 1234567890},
                            ])
                        else:
                            self.assertEqual(journal.events, [])
                self.assertEqual((path / 'lock').stat().st_ino, inode)

    def test_invalid_files_refuse_promptly_and_release_lock_in_same_process(self):
        # Child timeout bounds FIFOs. Refusal, raw lock reacquisition, exact
        # snapshot restoration and restart all happen in that same live child.
        code = '''
import fcntl, os, sys
from pathlib import Path
from personal_trading.tradelocker.journal import Journal
from personal_trading.tradelocker.tl_candidate import ReadOnlyError
root = Path(sys.argv[1])
cases = [(name, kind) for name in ('journal.json', 'lock')
         for kind in ('fifo', 'symlink', 'hardlink', 'permission')]
cases.append(('journal.json', 'corruption'))
for index, (name, kind) in enumerate(cases):
    path = root / str(index)
    event = {'kind': 'INTENT', 'approval_id': 'synthetic-file-guard'}
    with Journal(path, 'a' * 64) as journal:
        journal.append(event)
    inode = (path / 'lock').stat().st_ino
    target, saved = path / name, path / 'saved'
    snapshot = (path / 'journal.json').read_bytes()
    if kind == 'hardlink':
        os.link(target, saved)
    elif kind == 'permission':
        target.chmod(0o644)
    else:
        target.rename(saved)
        if kind == 'fifo':
            os.mkfifo(target, 0o600)
        elif kind == 'symlink':
            target.symlink_to(saved)
        else:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, b'SYNTHETIC_CORRUPT_SNAPSHOT')
            os.close(fd)
    try:
        with Journal(path, 'a' * 64):
            raise AssertionError('invalid file accepted')
    except ReadOnlyError as exc:
        assert exc.__context__ is None and exc.__cause__ is None
        assert 'SYNTHETIC_CORRUPT_SNAPSHOT' not in str(exc)
    lock_path = saved if name == 'lock' and kind in ('fifo', 'symlink') else path / 'lock'
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    if kind == 'hardlink':
        saved.unlink()
    elif kind == 'permission':
        target.chmod(0o600)
    else:
        target.unlink()
        saved.rename(target)
    assert (path / 'journal.json').read_bytes() == snapshot
    with Journal(path, 'a' * 64) as journal:
        assert journal.restarted and journal.events == [event]
    assert (path / 'lock').stat().st_ino == inode
print('9_FILE_CASES_REFUSED_AND_REACQUIRED_WITHOUT_RESET')
'''
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            child = subprocess.run(
                [sys.executable, '-B', '-W', 'error', '-c', code, root],
                cwd=Path(__file__).resolve().parents[2],
                env={'PATH': '/usr/bin:/bin', 'TMPDIR': tempfile.gettempdir()},
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stdout.strip(), '9_FILE_CASES_REFUSED_AND_REACQUIRED_WITHOUT_RESET')

    def test_missing_established_journal_refuses_without_resetting_history(self):
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root:
            path = Path(root) / 'state'
            events = [
                {'kind': 'INTENT', 'approval_id': 'synthetic-consumed-once'},
                {'kind': 'RATE', 'timestamp': 1234567890},
            ]
            with Journal(path, BINDING) as journal:
                for event in events:
                    journal.append(event)
            self.assertEqual(json.loads((path / 'journal.json').read_text()),
                             {'schema': 1, 'binding': BINDING, 'events': events})
            lock_inode = (path / 'lock').stat().st_ino
            # Preserve the exact synthetic snapshot for lock-release testing,
            # not an automatic recovery/reset facility.
            (path / 'journal.json').rename(path / 'saved-snapshot')
            with self.assertRaises(ReadOnlyError):
                with Journal(path, BINDING):
                    pass
            self.assertFalse((path / 'journal.json').exists())
            self.assertEqual((path / 'lock').stat().st_ino, lock_inode)
            # Same live process; a subprocess exit must not mask a leaked lock.
            fd = os.open(path / 'lock', os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            (path / 'saved-snapshot').rename(path / 'journal.json')
            with Journal(path, BINDING) as journal:
                self.assertTrue(journal.restarted)
                self.assertEqual(journal.events, events)
            self.assertEqual((path / 'lock').stat().st_ino, lock_inode)

    def test_state_loss_cannot_reuse_consumed_approval_in_real_controller(self):
        from personal_trading.tradelocker.demo_execution import (
            Controller, OwnerApproval, synthetic_reader,
        )
        from personal_tests.tradelocker.test_safety import policy_fixture
        import time

        policy, qualification = policy_fixture()
        with tempfile.TemporaryDirectory(prefix='hermes-journal-recovery-') as root, \
                synthetic_reader() as (reader, wire):
            assert reader is not None
            path = Path(root) / 'state'
            with Controller(reader, policy, qualification, path) as controller:
                action = controller.describe_submit()
                approval = OwnerApproval(
                    'synthetic-state-loss-reuse', action.request_digest,
                    controller.policy_digest, int(time.time()) + 60,
                    'APPROVE ONE DEMO REQUEST ' + action.request_digest
                    + ' POLICY ' + controller.policy_digest,
                )
                controller.submit(approval)
                self.assertTrue(any(e['kind'] == 'RATE' for e in controller.journal.events))
                self.assertTrue(any(e.get('approval_id') == approval.approval_id
                                    for e in controller.journal.events))
            lock_inode = (path / 'lock').stat().st_ino
            # A flat snapshot does not undo the prior approval consumption.
            wire.positions = []
            wire.orders = []
            (path / 'journal.json').unlink()
            refused = False
            try:
                with Controller(reader, policy, qualification, path) as controller:
                    controller.submit(approval)
            except ReadOnlyError:
                refused = True
            posts = [call for call in wire.calls
                     if call[0] == 'POST' and '/auth/' not in call[1]]
            self.assertEqual(len(posts), 1, 'Consumed approval produced another order POST')
            self.assertTrue(refused, 'Established state loss must refuse construction')
            self.assertFalse((path / 'journal.json').exists())
            self.assertEqual((path / 'lock').stat().st_ino, lock_inode)
            self.assertIsNone(reader._sdk._execution_guard)
            self.assertFalse(reader._sdk._execution_reads)


if __name__ == '__main__':
    unittest.main()
