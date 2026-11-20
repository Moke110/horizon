from pathlib import Path
from pypdf import PdfWriter
import pytest
from archive.store import ArchiveError
from pdf_ingest.ingest import _extract_pages


def test_encrypted_pdf_requires_explicit_supplement(tmp_path):
    path=tmp_path/'encrypted.pdf'
    writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.encrypt('secret');writer.write(path)
    with pytest.raises(ArchiveError,match='encrypted'):_extract_pages(path)
