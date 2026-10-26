"""Bounded Poppler extraction for approved PDF bytes; no OCR or model calls.

A short-lived isolated worker sets resource limits before exec. No preexec_fn
is used (parsing is called from an asyncio worker thread). Only this parser's
own child is settled on timeout. No source paths, passwords or secrets in argv.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 200_000

MAX_PAGES = 200
MAX_OUTPUT_BYTES = MAX_CHARS * 4
MEMORY_BYTES = 512 * 1024 * 1024
CPU_SECONDS = 2
WALL_SECONDS = 4
WORKER = Path(__file__).resolve()


def _worker(argv):
    # Run only in the isolated child, never change limits of the host process.
    import resource

    operation, executable, source, output = argv
    if operation not in ('info', 'text'):
        return 64
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    file_limit = 65536 if operation == 'info' else MAX_OUTPUT_BYTES
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if operation == 'info':
        command = [executable, '-enc', 'UTF-8', source]
    else:
        # Default form-feed boundaries preserve empty physical pages.
        command = [executable, '-enc', 'UTF-8', '-eol', 'unix', source, output]
    os.execv(executable, command)
    return 70


def _run(operation, executable, source, output):
    """Bound every process and file; discard sensitive diagnostics."""
    from librarian_sources import SourceError

    environment = {'LANG': 'C', 'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'}
    try:
        with output.open('xb') as stdout:
            result = subprocess.run(
                [sys.executable, '-I', str(WORKER), '--worker', operation,
                 executable, str(source), str(output)],
                stdin=subprocess.DEVNULL,
                stdout=stdout if operation == 'info' else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=WALL_SECONDS,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise SourceError('unavailable', 'PDF parsing exceeded its wall-time limit.') from exc
    except OSError as exc:
        raise SourceError('unavailable', 'The bounded PDF parser could not start.') from exc
    if result.returncode < 0:
        raise SourceError('too_large', 'PDF parser exceeded its resource bound.')
    if result.returncode != 0:
        raise SourceError('unsupported', 'PDF is corrupt, encrypted, unreadable, or unsupported.')
    bound = 65536 if operation == 'info' else MAX_OUTPUT_BYTES
    with output.open('rb') as stream:
        data = stream.read(bound + 1)
    if len(data) >= bound:
        raise SourceError('too_large', 'PDF parser output exceeded its bound.')
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise SourceError('unsupported', 'PDF parser returned invalid UTF-8.') from exc


def parse_pdf(raw):
    """Approved bytes -> exactly one text per physical page, including blanks."""
    from librarian_sources import SourceError

    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise SourceError('too_large', 'PDF input exceeds the 10 MiB limit.')
    if b'%PDF-' not in raw[:1024]:
        raise SourceError('unsupported', 'Input is not a readable PDF.')
    if os.name != 'posix':
        raise SourceError('unsupported', 'Bounded PDF parsing requires a POSIX runtime.')
    info = shutil.which('pdfinfo')
    text = shutil.which('pdftotext')
    if not info or not text:
        raise SourceError('unsupported', 'PDF parsing requires installed Poppler pdfinfo and pdftotext.')
    # Parent preflight; child sets hard limits or fails closed before parser exec.
    try:
        import resource
    except ImportError as exc:
        raise SourceError('unsupported', 'PDF parser resource limits are unavailable.') from exc
    with tempfile.TemporaryDirectory(prefix='jarvis-librarian-pdf-') as temporary:
        root = Path(temporary)
        source = root / 'input.pdf'
        source.write_bytes(raw)
        metadata = _run('info', info, source, root / 'info.txt')
        encrypted = re.search(r'^Encrypted:\s*(\S+)', metadata, re.MULTILINE)
        if encrypted is None or encrypted[1] != 'no':
            raise SourceError('unsupported', 'Encrypted PDFs are outside this parser scope.')
        count = re.search(r'^Pages:\s*([0-9]+)\s*$', metadata, re.MULTILINE)
        if count is None:
            raise SourceError('unsupported', 'PDF physical page count could not be verified.')
        page_count = int(count[1])
        if not 1 <= page_count <= MAX_PAGES:
            raise SourceError('too_large', 'PDF must contain 1–200 physical pages.')
        extracted = _run('text', text, source, root / 'text.txt')
        if len(extracted) > MAX_CHARS:
            raise SourceError('too_large', 'PDF text exceeds the 200,000 character extraction limit.')
        if not extracted.endswith('\f'):
            raise SourceError('unsupported', 'PDF page boundaries could not be verified.')
        pages = extracted[:-1].split('\f')
        if len(pages) != page_count:
            raise SourceError('unsupported', 'PDF text/page boundaries are inconsistent.')
        if not any(page.strip() for page in pages):
            raise SourceError('unsupported', 'PDF has no extractable text; blank/scanned files need separately approved OCR.')
        return pages


if __name__ == '__main__':
    if len(sys.argv) == 6 and sys.argv[1] == '--worker':
        raise SystemExit(_worker(sys.argv[2:]))
    raise SystemExit(64)
