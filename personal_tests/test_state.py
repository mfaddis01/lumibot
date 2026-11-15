"""Real local-filesystem/process tests; no broker lifecycle is simulated."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LocalStateTests(unittest.TestCase):
    def test_lock_excludes_another_process_and_is_released_on_exit(self):
        from personal_trading.state import exclusive_executor
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-c", (
                "import sys; from personal_trading.state import exclusive_executor; "
                "\ntry:\n with exclusive_executor(sys.argv[1]): print('acquired')"
                "\nexcept RuntimeError: print('blocked')"), directory]
            with exclusive_executor(directory):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.stdout.strip(), "blocked", result.stderr)
                with self.assertRaises(RuntimeError):
                    with exclusive_executor(directory):
                        self.fail("second local lock acquired")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "acquired", result.stderr)
            self.assertTrue((Path(directory) / "executor.lock").exists())
    def test_durable_identity_survives_restart_and_never_silently_rebinds(self):
        import json
        from personal_trading.state import bound_executor
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                with bound_executor(directory, "a" * 64):
                    pass
            path = Path(directory) / "identity.json"
            self.assertEqual(json.loads(path.read_text()), {"schema": 1, "config_identity": "a" * 64})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            previous = path.read_bytes()
            with self.assertRaises(RuntimeError):
                with bound_executor(directory, "b" * 64):
                    self.fail("state rebound")
            self.assertEqual(path.read_bytes(), previous)
            path.write_text("{corrupt")
            with self.assertRaises(RuntimeError):
                with bound_executor(directory, "a" * 64):
                    self.fail("corrupt state ignored")
            self.assertEqual(path.read_text(), "{corrupt")

    def test_unsafe_lock_paths_fail_closed_and_crash_releases_kernel_lock(self):
        from personal_trading.state import exclusive_executor
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside"
            target.write_text("unchanged")
            lock = root / "executor.lock"
            lock.symlink_to(target)
            with self.assertRaises(RuntimeError):
                with exclusive_executor(root):
                    self.fail("symlink lock accepted")
            self.assertEqual(target.read_text(), "unchanged")
            lock.unlink()
            root.chmod(0o755)
            try:
                with self.assertRaises(RuntimeError):
                    with exclusive_executor(root):
                        self.fail("non-private state directory accepted")
            finally:
                root.chmod(0o700)
            command = [sys.executable, "-c", (
                "import sys,time; from personal_trading.state import exclusive_executor; "
                "\nwith exclusive_executor(sys.argv[1]):\n print('held',flush=True); time.sleep(60)"), directory]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), "held")
                process.kill()
                process.communicate(timeout=5)
                with exclusive_executor(root):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)
    def test_fifo_identity_is_rejected_within_deadline_and_releases_lock(self):
        from textwrap import dedent
        from personal_trading.state import exclusive_executor

        code = dedent("""
            import sys
            sys.path.insert(0, sys.argv[1])
            from personal_trading.state import bound_executor, exclusive_executor
            for attempt in range(2):
                try:
                    with bound_executor(sys.argv[2], 'a' * 64):
                        raise AssertionError('FIFO identity accepted')
                except RuntimeError as error:
                    assert str(error) == ('state identity unavailable or mismatched; '
                                          'operator review required')
                    print('rejected', flush=True)
                # Reacquire while the failing process is still alive: process exit
                # alone must not be what releases ownership after a bad record.
                with exclusive_executor(sys.argv[2]):
                    print('reacquired', flush=True)
        """)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identity = root / "identity.json"
            os.mkfifo(identity, 0o600)  # Real FIFO, deliberately no writer.
            command = [sys.executable, "-I", "-B", "-W", "error", "-c", code,
                       str(Path(__file__).resolve().parents[1]), directory]
            environment = {key: os.environ[key] for key in ("PATH", "TMPDIR") if key in os.environ}
            try:
                result = subprocess.run(command, capture_output=True, text=True,
                                        env=environment, timeout=2)
            except subprocess.TimeoutExpired:
                # subprocess.run kills/reaps the child on timeout; never hang the suite.
                self.fail("FIFO identity blocked beyond the two-second child deadline")
            finally:
                with exclusive_executor(root):
                    pass
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), ["rejected", "reacquired", "rejected", "reacquired"])
            import stat
            self.assertTrue(stat.S_ISFIFO(identity.lstat().st_mode))

    def test_binding_refuses_release_local_state_and_ambiguous_metadata(self):
        from personal_trading.state import bound_executor
        release_local = Path(__file__).resolve().parents[1] / "personal_trading" / "forbidden-state"
        from unittest.mock import patch
        with patch("pathlib.Path.mkdir", side_effect=AssertionError("release-local state attempted")):
            with self.assertRaises(RuntimeError):
                with bound_executor(release_local, "a" * 64):
                    self.fail("state inside a disposable release accepted")
        self.assertFalse(release_local.exists())
        with tempfile.TemporaryDirectory() as directory:
            with bound_executor(directory, "a" * 64):
                pass
            path = Path(directory) / "identity.json"
            for text in ('{"schema":true,"config_identity":"' + "a" * 64 + '"}',
                         '{"schema":0,"schema":1,"config_identity":"' + "a" * 64 + '"}'):
                path.write_text(text)
                with self.assertRaises(RuntimeError):
                    with bound_executor(directory, "a" * 64):
                        self.fail("ambiguous metadata accepted")
                self.assertEqual(path.read_text(), text)


if __name__ == "__main__":
    unittest.main()
