import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from personal_trading.tradelocker.tl_candidate import ReadOnlyError
SCRATCH = tempfile.gettempdir()


class JournalTests(unittest.TestCase):
    def test_private_durable_state_lock_and_restart_detection(self):
        from personal_trading.tradelocker.journal import Journal
        with tempfile.TemporaryDirectory(prefix='hermes-journal-', dir=SCRATCH) as root:
            path = Path(root)/'private'
            with Journal(path, 'a'*64) as journal:
                self.assertFalse(journal.restarted)
                journal.append({'kind': 'INTENT', 'approval_id': 'synthetic-once'})
                self.assertEqual(stat.S_IMODE((path/'journal.json').stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
                child = subprocess.run([sys.executable, '-B', '-W', 'error', '-c',
                    "from personal_trading.tradelocker.journal import Journal; from personal_trading.tradelocker.tl_candidate import ReadOnlyError\ntry:\n Journal(__import__('pathlib').Path(__import__('sys').argv[1]), 'a'*64)\nexcept ReadOnlyError:\n print('LOCKED')\nelse:\n raise AssertionError('second writer')", str(path)],
                    cwd=Path(__file__).resolve().parents[2], env={'PATH':'/usr/bin:/bin'},
                    capture_output=True, text=True, timeout=5)
                self.assertEqual(child.returncode, 0, child.stderr)
                self.assertEqual(child.stdout.strip(), 'LOCKED')
            with Journal(path, 'a'*64) as journal:
                self.assertTrue(journal.restarted)
                self.assertEqual(journal.events, [{'kind':'INTENT','approval_id':'synthetic-once'}])
            with self.assertRaises(ReadOnlyError):
                Journal(path, 'b'*64)
            with Journal(path, 'a'*64):
                pass

    def test_process_death_releases_persistent_lock_without_erasing_intent(self):
        from personal_trading.tradelocker.journal import Journal
        import select
        with tempfile.TemporaryDirectory(prefix='hermes-journal-',dir=SCRATCH) as root:
            path=Path(root)/'private'
            code="from personal_trading.tradelocker.journal import Journal; from pathlib import Path; import sys; j=Journal(Path(sys.argv[1]),'a'*64); j.append({'kind':'INTENT','approval_id':'synthetic-death'}); print('READY',flush=True); sys.stdin.read()"
            child=subprocess.Popen([sys.executable,'-B','-W','error','-c',code,str(path)],
                cwd=Path(__file__).resolve().parents[2],env={'PATH':'/usr/bin:/bin'},
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                self.assertTrue(select.select([child.stdout],[],[],5)[0])
                self.assertEqual(child.stdout.readline().strip(),'READY')
                child.kill()
                child.communicate(timeout=5)
                with Journal(path,'a'*64) as journal:
                    self.assertTrue(journal.restarted)
                    self.assertEqual(journal.events[-1]['approval_id'],'synthetic-death')
            finally:
                if child.poll() is None: child.kill()
                child.communicate(timeout=5)

    def test_nonfinite_json_state_refuses_instead_of_resetting_budget(self):
        from personal_trading.tradelocker.journal import Journal
        with tempfile.TemporaryDirectory(prefix='hermes-journal-',dir=SCRATCH) as root:
            path=Path(root)/'private'
            with Journal(path,'a'*64): pass
            target=path/'journal.json'
            target.write_text('{"schema":1,"binding":"'+('a'*64)+'","events":[{"kind":"RATE","timestamp":NaN}]}')
            with self.assertRaises(ReadOnlyError): Journal(path,'a'*64)

    def test_fifo_symlink_permissions_corruption_refuse_and_release_lock(self):
        from personal_trading.tradelocker.journal import Journal
        with tempfile.TemporaryDirectory(prefix='hermes-journal-', dir=SCRATCH) as root:
            path=Path(root)/'private'
            with Journal(path, 'a'*64):
                pass
            target=path/'journal.json'
            snapshot=target.read_bytes()
            target.unlink()
            os.mkfifo(target, 0o600)
            code="""from pathlib import Path
from personal_trading.tradelocker.journal import Journal
from personal_trading.tradelocker.tl_candidate import ReadOnlyError
import sys
p=Path(sys.argv[1])
try:
 Journal(p, 'a'*64)
except ReadOnlyError:
 pass
else:
 raise AssertionError('FIFO accepted')
(p/'journal.json').unlink()
(p/'journal.json').write_text(sys.argv[2])
(p/'journal.json').chmod(0o600)
with Journal(p, 'a'*64):
 print('REFUSED_AND_REACQUIRED')
"""
            child=subprocess.run([sys.executable,'-B','-W','error','-c',code,str(path),snapshot.decode()],
                cwd=Path(__file__).resolve().parents[2],env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,timeout=5)
            self.assertEqual(child.returncode,0,child.stderr)
            self.assertEqual(child.stdout.strip(),'REFUSED_AND_REACQUIRED')
            target.unlink()
            target.symlink_to(path/'lock')
            with self.assertRaises(ReadOnlyError): Journal(path,'a'*64)
            target.unlink()
            target.write_text('private garbage')
            target.chmod(0o600)
            caught=None
            try: Journal(path,'a'*64)
            except ReadOnlyError as exc: caught=exc
            self.assertIsNotNone(caught)
            self.assertIsNone(caught.__context__)
            self.assertNotIn('private garbage',str(caught))
            target.unlink()
            target.write_bytes(snapshot)
            target.chmod(0o600)
            with Journal(path,'a'*64): pass
            target.chmod(0o644)
            with self.assertRaises(ReadOnlyError): Journal(path,'a'*64)


if __name__=='__main__': unittest.main()
