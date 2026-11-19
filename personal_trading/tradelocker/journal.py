"""Private same-host cooperative single-writer journal, not broker idempotency.

Use a persistent owner-only directory outside releases with trusted ancestors.
Never delete/replace the lock inode or reset a journal to retry an operation.
Atomic snapshot replacement + file/directory fsync precede every write permit.
First use exclusively creates the directory beneath an already durable parent.
Existing directories require both lock and state; missing state needs recovery,
not automatic reinitialization. Initialization fsyncs the new parent entry too.
"""
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import uuid
from personal_trading.tradelocker.tl_candidate import ReadOnlyError


class Journal:
    def __init__(self, directory, binding_digest):
        self._lock = None
        self._dir = None
        self._closed = True
        failed = False
        try:
            if not re.fullmatch('[a-f0-9]{64}', binding_digest):
                raise ValueError()
            path = Path(directory)
            # Only this call's exclusive mkdir establishes first use. An empty
            # existing directory can be interrupted initialization or lost state.
            # Reject symlink directory; ancestor trust remains caller's duty.
            try:
                path.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                created = False
            self._dir = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            s = os.fstat(self._dir)
            if s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
                raise ValueError()
            self._lock = self._open_private('lock', create=created)
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if created:
                # Persist the initialization marker and its parent entry before
                # publishing state. An interrupted initialization stays closed.
                os.fsync(self._lock)
                os.fsync(self._dir)
                parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            self._closed = False
            try:
                fd = self._open_private('journal.json')
            except FileNotFoundError:
                if not created:
                    raise ValueError()
                self.restarted = False
                self._state = {'schema':1,'binding':binding_digest,'events':[]}
                self._persist()
            else:
                self.restarted = True
                with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                    state = json.load(stream, object_pairs_hook=self._unique,
                                      parse_constant=self._invalid_constant)
                if (type(state) is not dict or set(state) != {'schema','binding','events'} or
                        type(state['schema']) is not int or state['schema'] != 1 or
                        state['binding'] != binding_digest or type(state['events']) is not list or
                        any(type(e) is not dict or type(e.get('kind')) is not str for e in state['events'])):
                    raise ValueError()
                self._state = state
        except (OSError, ValueError, TypeError, RuntimeError):
            failed = True
        if failed:
            self.close()
            raise ReadOnlyError('Private journal unavailable, locked, corrupt or mismatched')

    @staticmethod
    def _invalid_constant(value):
        raise ValueError('Nonfinite journal JSON refused')

    @staticmethod
    def _unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def _open_private(self, name, create=False):
        flags = os.O_RDWR if create else os.O_RDONLY
        flags |= os.O_NOFOLLOW | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(name, flags, 0o600, dir_fd=self._dir)
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600:
            os.close(fd)
            raise ValueError()
        return fd

    @property
    def events(self):
        self.require_open()
        return deepcopy(self._state['events'])

    def require_open(self):
        if self._closed:
            raise ReadOnlyError('Journal lock is not held')

    def append(self, event):
        self.require_open()
        if type(event) is not dict or type(event.get('kind')) is not str:
            raise ReadOnlyError('Typed journal event required')
        # Never retain arbitrary caller objects; JSON is the durable interface.
        failed = False
        try:
            encoded = json.dumps(event, allow_nan=False)
            self._state['events'].append(json.loads(encoded))
            self._persist()
        except (OSError, TypeError, ValueError):
            failed = True
        if failed:
            self.close()
            raise ReadOnlyError('Journal durability failed; no further writes permitted')

    def _persist(self):
        name = '.journal-' + uuid.uuid4().hex
        fd = None
        try:
            data = json.dumps(self._state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._dir)
            with os.fdopen(fd, 'wb') as stream:
                fd = None
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, 'journal.json', src_dir_fd=self._dir, dst_dir_fd=self._dir)
            os.fsync(self._dir)
        finally:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(name, dir_fd=self._dir)
            except FileNotFoundError:
                pass

    def close(self):
        # A failed contender must not unlock a lock it never acquired; closing
        # its own descriptor is enough. The persistent inode is not unlinked.
        self._closed = True
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        if self._dir is not None:
            os.close(self._dir)
            self._dir = None

    def __enter__(self):
        self.require_open()
        return self

    def __exit__(self, *args):
        self.close()
