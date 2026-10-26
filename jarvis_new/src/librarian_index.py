"""Bounded derived librarian state; no original file or OAuth mutations."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

MAX_SOURCES = 100
MAX_CHUNKS = 512


class Index:
    """Memory by default; explicit private derived-state path for host use."""

    def __init__(self, path: str | Path = ':memory:'):
        self.lock = threading.RLock()
        if str(path) != ':memory:':
            target = Path(path)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if target.is_symlink():
                raise ValueError('Derived index cannot be a symlink')
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600) if not target.exists() else None
            if fd is not None:
                os.close(fd)
            elif target.stat().st_mode & 0o077:
                raise ValueError('Existing derived index must be private')
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        # Rollback journal, not a long-lived WAL; removed content has no retained WAL.
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS sources (
                ref TEXT PRIMARY KEY, grant_id TEXT NOT NULL, key TEXT NOT NULL,
                meta TEXT NOT NULL, indexed_version TEXT, indexed_at REAL,
                digest TEXT, digest_kind TEXT, UNIQUE(grant_id,key));
            CREATE TABLE IF NOT EXISTS chunks (
                ref TEXT NOT NULL, ordinal INTEGER NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(ref,ordinal));
        ''')
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def sources(self, grant_id=None):
        with self.lock:
            query = 'SELECT ref,grant_id,key,meta,indexed_version,indexed_at,digest,digest_kind FROM sources'
            params = ()
            if grant_id is not None:
                query += ' WHERE grant_id=?'
                params = (grant_id,)
            rows = self.db.execute(query + ' ORDER BY ref LIMIT 100', params).fetchall()
            return [dict(ref=r[0], grant_id=r[1], key=r[2], meta=json.loads(r[3]),
                         indexed_version=r[4], indexed_at=r[5], digest=r[6], digest_kind=r[7]) for r in rows]

    def get(self, ref):
        return next((row for row in self.sources() if row['ref'] == ref), None)

    def upsert(self, ref, grant_id, key, meta):
        with self.lock, self.db:
            old = self.db.execute('SELECT ref FROM sources WHERE grant_id=? AND key=?', (grant_id,key)).fetchone()
            if old:
                ref = old[0]
            elif self.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] >= MAX_SOURCES:
                return None
            self.db.execute('INSERT INTO sources(ref,grant_id,key,meta) VALUES(?,?,?,?) '
                            'ON CONFLICT(ref) DO UPDATE SET meta=excluded.meta',
                            (ref,grant_id,key,json.dumps(meta)))
            return ref

    def invalidate(self, ref):
        with self.lock, self.db:
            self.db.execute('DELETE FROM chunks WHERE ref=?', (ref,))
            self.db.execute('UPDATE sources SET indexed_version=NULL,indexed_at=NULL,digest=NULL,digest_kind=NULL WHERE ref=?',(ref,))

    def purge(self, grant_id):
        with self.lock, self.db:
            self.db.execute('DELETE FROM chunks WHERE ref IN (SELECT ref FROM sources WHERE grant_id=?)',(grant_id,))
            self.db.execute('DELETE FROM sources WHERE grant_id=?',(grant_id,))

    def replace(self, ref, version, chunks, digest, digest_kind='identical_bytes', fail=False, indexed_at=None):
        if len(chunks) > MAX_CHUNKS:
            raise ValueError('Chunk limit exceeded')
        with self.lock, self.db:
            if not self.get(ref):
                raise ValueError('Unknown source')
            self.db.execute('DELETE FROM chunks WHERE ref=?',(ref,))
            self.db.executemany('INSERT INTO chunks VALUES(?,?,?)',
                                [(ref,n,json.dumps(c)) for n,c in enumerate(chunks)])
            if fail:
                raise RuntimeError('Injected transaction failure')
            self.db.execute('UPDATE sources SET indexed_version=?,indexed_at=?,digest=?,digest_kind=? WHERE ref=?',
                            (version,time.time() if indexed_at is None else indexed_at,digest,digest_kind,ref))

    def chunks(self, ref):
        with self.lock:
            rows=self.db.execute('SELECT data FROM chunks WHERE ref=? ORDER BY ordinal LIMIT 512',(ref,)).fetchall()
            return [json.loads(r[0]) for r in rows]

    def chunk_count(self):
        with self.lock:
            return self.db.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
