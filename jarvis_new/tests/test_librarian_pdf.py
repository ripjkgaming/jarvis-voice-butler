"""Real Poppler extraction; every PDF is generated here from synthetic bytes."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from librarian import Grant, Librarian
from librarian_index import Index
from librarian_pdf import parse_pdf, _run, WALL_SECONDS, MAX_OUTPUT_BYTES
from librarian_sources import DriveSource, LocalSource, SourceError, page_chunks


def make_pdf(pages, image=False, page_width=300):
    """Tiny standards-shaped fixture writer, not a user document authoring tool."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    ids = []
    for text in pages:
        page_id = len(objects) + 1
        stream_id = page_id + 1
        ids.append(page_id)
        if image:
            image_id = page_id + 2
            stream = b"q 200 0 0 200 40 40 cm /Im0 Do Q"
            resources = f"<< /XObject << /Im0 {image_id} 0 R >> >>"
        else:
            safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream = (
                f"BT /F1 12 Tf 40 140 Td ({safe}) Tj ET".encode("ascii")
                if text
                else b""
            )
            resources = "<< /Font << /F1 3 0 R >> >>"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} 200] /Resources {resources} /Contents {stream_id} 0 R >>".encode()
        )
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
        if image:
            objects.append(
                b"<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 1 >>\nstream\n\x00\nendstream"
            )
    kids = " ".join(f"{i} 0 R" for i in ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(ids)} >>".encode()
    data = bytearray(b"%PDF-1.4\n% synthetic Jarvis fixture\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


@unittest.skipUnless(
    shutil.which("pdfinfo") and shutil.which("pdftotext"),
    "Installed Poppler required; no package installs",
)
class RealPDFTests(unittest.TestCase):
    def test_real_four_page_extraction_preserves_blank_page(self):
        pages = parse_pdf(
            make_pdf(["Intro", "", "Voltage notes", "Resistance limits current."])
        )
        self.assertEqual(len(pages), 4)
        self.assertFalse(pages[1].strip())
        self.assertIn("Resistance limits current.", pages[3])
        chunks = page_chunks(pages)
        self.assertEqual([c["locator"]["page"] for c in chunks], [1, 3, 4])

    def test_real_service_answer_has_exact_page_four_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "circuit.pdf"
            path.write_bytes(
                make_pdf(["Intro", "Second", "Third", "Resistance limits current."])
            )
            service = Librarian(Index(), {"local": LocalSource()})
            try:
                service.approve(
                    Grant("pdf", "local", root, content=True), confirmed=True
                )
                ref = service.find("circuit")["candidates"][0]["ref"]
                answer = service.answer(ref, "what does page four say", "page 4")
                self.assertEqual(answer["status"], "ok")
                self.assertEqual(answer["evidence"][0]["locator"], {"page": 4})
                self.assertEqual(
                    answer["evidence"][0]["quote"].strip(), "Resistance limits current."
                )
                self.assertEqual(
                    service.answer(ref, "current")["evidence"][0]["locator"],
                    {"page": 4},
                )
            finally:
                service.close()

    def test_real_drive_stored_pdf_uses_media_and_page_provenance(self):
        calls = []
        raw = make_pdf(["First", "Budget expenses here."])
        meta = {
            "id": "pdf123",
            "name": "Budget",
            "mimeType": "application/pdf",
            "parents": ["folder123"],
            "version": "1",
            "modifiedTime": "v1",
            "size": len(raw),
            "webViewLink": "https://drive.google.com/file/d/pdf123/view",
        }

        def transport(account, resource, params, media=False):
            calls.append((account, resource, params, media))
            if resource == "files":
                return {"files": [meta]}
            return raw if media else dict(meta)

        service = Librarian(Index(), {"drive": DriveSource(transport)})
        try:
            service.approve(
                Grant("drive", "drive", "folder123", account="school", content=True),
                confirmed=True,
            )
            ref = service.find("Budget")["candidates"][0]["ref"]
            result = service.answer(ref, "expenses", "page 2")
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["evidence"][0]["locator"], {"page": 2})
            self.assertTrue(all(call[0] == "school" for call in calls))
            self.assertEqual(sum(call[3] for call in calls), 1)
        finally:
            service.close()

    def test_blank_and_scanned_pdf_fail_without_ocr(self):
        for raw in [make_pdf(["", ""]), make_pdf(["scan"], image=True)]:
            with self.assertRaises(SourceError) as error:
                parse_pdf(raw)
            self.assertEqual(error.exception.status, "unsupported")
            self.assertIn("no extractable text", str(error.exception))

    def test_corrupt_empty_and_oversize_inputs_fail_closed(self):
        for raw, status in [
            (b"", "unsupported"),
            (b"not a PDF", "unsupported"),
            (b"%PDF-1.4\ncorrupt", "unsupported"),
            (b"%PDF-1.4\n" + b"x" * (10 * 1024 * 1024), "too_large"),
        ]:
            with self.assertRaises(SourceError) as error:
                parse_pdf(raw)
            self.assertEqual(error.exception.status, status)

    def test_page_cap_prevents_text_child(self):
        raw = make_pdf(["fixture"] * 201)
        import librarian_pdf

        original = librarian_pdf._run
        operations = []

        def record(operation, *args):
            operations.append(operation)
            return original(operation, *args)

        with patch("librarian_pdf._run", record):
            with self.assertRaises(SourceError) as error:
                parse_pdf(raw)
        self.assertEqual(error.exception.status, "too_large")
        self.assertEqual(operations, ["info"])

    def test_real_character_output_limit_is_enforced(self):
        raw = make_pdf(["X" * 200001], page_width=3000000)
        with self.assertRaises(SourceError) as error:
            parse_pdf(raw)
        self.assertEqual(error.exception.status, "too_large")

    def test_private_temporary_files_are_removed_on_success_and_failure(self):
        import librarian_pdf

        with tempfile.TemporaryDirectory() as parent:
            original = tempfile.TemporaryDirectory

            def directory(*args, **kwargs):
                return original(*args, dir=parent, **kwargs)

            with patch("librarian_pdf.tempfile.TemporaryDirectory", directory):
                parse_pdf(make_pdf(["fixture"]))
                self.assertEqual(list(Path(parent).iterdir()), [])
                with self.assertRaises(SourceError):
                    parse_pdf(b"%PDF-1.4\ncorrupt")
                self.assertEqual(list(Path(parent).iterdir()), [])


class PDFBoundTests(unittest.TestCase):
    def test_missing_poppler_does_not_install_or_start_children(self):
        with (
            patch("librarian_pdf.shutil.which", return_value=None),
            patch("librarian_pdf.subprocess.run") as run,
        ):
            with self.assertRaises(SourceError) as error:
                parse_pdf(make_pdf(["fixture"]))
        self.assertEqual(error.exception.status, "unsupported")
        run.assert_not_called()

    def test_timeout_is_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch(
                "librarian_pdf.subprocess.run",
                side_effect=subprocess.TimeoutExpired("sensitive args", WALL_SECONDS),
            ) as run:
                with self.assertRaises(SourceError) as error:
                    _run("info", "/usr/bin/pdfinfo", root / "input.pdf", root / "out")
            self.assertEqual(error.exception.status, "unavailable")
            self.assertNotIn("sensitive", str(error.exception))
            self.assertEqual(run.call_args.kwargs["timeout"], WALL_SECONDS)
            self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
            self.assertNotIn("preexec_fn", run.call_args.kwargs)

    def test_output_cap_and_page_boundary_mismatch_fail(self):
        def fake(operation, *args):
            if operation == "info":
                return "Encrypted: no\nPages: 2\n"
            return "only one page\f"

        with (
            patch("librarian_pdf.shutil.which", return_value="/usr/bin/fixture"),
            patch("librarian_pdf._run", fake),
        ):
            with self.assertRaises(SourceError) as error:
                parse_pdf(make_pdf(["one", "two"]))
        self.assertEqual(error.exception.status, "unsupported")
        with patch(
            "librarian_pdf._run",
            side_effect=["Encrypted: no\nPages: 1\n", "x" * 200001 + "\f"],
        ):
            with self.assertRaises(SourceError) as error:
                parse_pdf(make_pdf(["fixture"]))
        self.assertEqual(error.exception.status, "too_large")
