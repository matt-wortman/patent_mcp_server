"""Behavior tests for native-text-first ODP file-wrapper downloads."""

import io
import os
import tarfile
import zipfile

import httpx
import pytest

import patent_mcp_server.patents as patents
from patent_mcp_server.uspto import document_download as downloads


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

APP_NUM = "18533474"
DOCUMENT_ID = "MM26RN3L138X163"


def _xml_archive(member_name: str = "wrapper/Non-Final Rejection.xml") -> bytes:
    content = b"<office-action><claim>1</claim></office-action>"
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        info = tarfile.TarInfo(member_name)
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))
    return archive.getvalue()


def _word_document(text: bytes = b"native-word-content") -> bytes:
    document = io.BytesIO()
    with zipfile.ZipFile(document, mode="w") as bundle:
        bundle.writestr("[Content_Types].xml", b"<Types/>")
        bundle.writestr("word/document.xml", b"<document>" + text + b"</document>")
    return document.getvalue()


def _truncated_xml_archive() -> bytes:
    archive = _xml_archive()
    return archive[:700]


def _unsupported_compression_word_document() -> bytes:
    """Patch a valid OOXML ZIP to advertise an unsupported compression method."""
    content = bytearray(_word_document())
    position = 0
    while True:
        position = content.find(b"PK\x03\x04", position)
        if position < 0:
            break
        content[position + 8 : position + 10] = (99).to_bytes(2, "little")
        position += 4
    position = 0
    while True:
        position = content.find(b"PK\x01\x02", position)
        if position < 0:
            break
        content[position + 10 : position + 12] = (99).to_bytes(2, "little")
        position += 4
    return bytes(content)


def _oversized_word_document() -> bytes:
    document = io.BytesIO()
    with zipfile.ZipFile(document, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", b"<Types/>")
        oversized = b"x" * (downloads.MAX_NATIVE_MEMBER_BYTES + 1)
        bundle.writestr("word/document.xml", b"<document>" + oversized + b"</document>")
    return document.getvalue()


def _corrupt_deflated_word_document() -> bytes:
    document = io.BytesIO()
    with zipfile.ZipFile(document, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("[Content_Types].xml", b"<Types/>")
        bundle.writestr("word/document.xml", b"<document>native text</document>")
    content = bytearray(document.getvalue())
    with zipfile.ZipFile(io.BytesIO(content)) as bundle:
        info = bundle.getinfo("word/document.xml")
        offset = info.header_offset
        filename_length = int.from_bytes(content[offset + 26 : offset + 28], "little")
        extra_length = int.from_bytes(content[offset + 28 : offset + 30], "little")
        data_offset = offset + 30 + filename_length + extra_length
        content[data_offset] ^= 0xFF
    return bytes(content)


def _document_listing(*formats: str):
    urls = {
        "XML": (
            f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/xmlarchive"
        ),
        "MS_WORD": (
            f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
            "Non-Final%20Rejection.docx"
        ),
        "PDF": f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}.pdf",
    }
    return {
        "documentBag": [
            {
                "applicationNumberText": APP_NUM,
                "directionCategory": "OUTGOING",
                "documentCode": "CTNF",
                "documentCodeDescriptionText": "Non-Final Rejection",
                "documentIdentifier": DOCUMENT_ID,
                "officialDate": "2026-02-26T00:00:00.000-0500",
                "downloadOptionBag": [
                    {
                        "mimeTypeIdentifier": format_name,
                        "downloadUrl": urls[format_name],
                    }
                    for format_name in formats
                ],
            }
        ]
    }


def _install_listing(monkeypatch, listing):
    async def get_documents(url, *args, **kwargs):
        return listing

    monkeypatch.setattr(patents.api_client, "make_request", get_documents)


async def test_download_prefers_xml_and_returns_extracted_readable_file(
    monkeypatch, tmp_path
):
    """Regression: choosing PDF while native XML is available must fail."""
    _install_listing(monkeypatch, _document_listing("PDF", "MS_WORD", "XML"))

    async def download(url):
        if url.endswith("/xmlarchive"):
            content = _xml_archive()
            return {
                "content": content,
                "content_type": "application/octet-stream",
                "size_bytes": len(content),
            }
        return {"error": True, "message": "unexpected non-XML download"}

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["success"] is True
    assert result["selected_format"] == "XML"
    assert result["fallback_used"] is False
    assert result["attempted_formats"] == ["XML"]
    assert result["file_path"].endswith(".xml")
    assert open(result["file_path"], "rb").read() == (
        b"<office-action><claim>1</claim></office-action>"
    )


async def test_download_falls_back_to_word_when_xml_download_fails(
    monkeypatch, tmp_path
):
    """Regression: an XML failure must not skip an available native Word file."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD", "PDF"))

    async def download(url):
        if url.endswith("/xmlarchive"):
            return {
                "content": b"not a tar archive or raw XML",
                "content_type": "application/octet-stream",
                "size_bytes": 28,
            }
        if url.endswith(".docx"):
            content = _word_document()
            return {
                "content": content,
                "content_type": "application/octet-stream",
                "size_bytes": len(content),
            }
        content = b"%PDF-1.7 fallback"
        return {
            "content": content,
            "content_type": "application/pdf",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["fallback_used"] is True
    assert result["attempted_formats"] == ["XML", "MS_WORD"]
    assert result["file_path"].endswith(".docx")
    with zipfile.ZipFile(result["file_path"]) as document:
        assert b"native-word-content" in document.read("word/document.xml")


async def test_download_uses_pdf_when_no_native_text_option_exists(
    monkeypatch, tmp_path
):
    """Regression: the native preference must preserve PDF-only documents."""
    _install_listing(monkeypatch, _document_listing("PDF"))

    async def download(url):
        content = b"%PDF-1.7 fallback"
        return {
            "content": content,
            "content_type": "application/pdf",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "PDF"
    assert result["fallback_used"] is False
    assert result["attempted_formats"] == ["PDF"]
    assert result["file_path"].endswith(".pdf")
    assert open(result["file_path"], "rb").read() == b"%PDF-1.7 fallback"


async def test_explicit_word_preference_still_falls_back_if_needed(
    monkeypatch, tmp_path
):
    """Regression: callers must be able to prefer Word without disabling fallback."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD", "PDF"))

    async def download(url):
        if url.endswith(".docx"):
            content = _word_document(b"preferred-word")
            return {
                "content": content,
                "content_type": "application/octet-stream",
                "size_bytes": len(content),
            }
        return {"error": True, "message": "unexpected fallback"}

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(
        APP_NUM, DOCUMENT_ID, preferred_format="MS_WORD"
    )

    assert result["selected_format"] == "MS_WORD"
    assert result["fallback_used"] is False
    assert result["attempted_formats"] == ["MS_WORD"]


async def test_raised_network_error_falls_back_to_next_native_format(
    monkeypatch, tmp_path
):
    """Regression: an exhausted XML timeout must not abort Word fallback."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD", "PDF"))

    async def download(url):
        if url.endswith("/xmlarchive"):
            raise httpx.ConnectTimeout("USPTO XML timeout")
        content = _word_document()
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["attempted_formats"] == ["XML", "MS_WORD"]


async def test_malformed_raw_xml_falls_back_to_word(monkeypatch, tmp_path):
    """Regression: a payload beginning with '<' is not necessarily valid XML."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD", "PDF"))

    async def download(url):
        if url.endswith("/xmlarchive"):
            content = b"<not well formed"
        else:
            content = _word_document()
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["attempted_formats"] == ["XML", "MS_WORD"]


async def test_unknown_xml_encoding_falls_back_to_word(monkeypatch, tmp_path):
    """Regression: unsupported XML encodings must become controlled fallbacks."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD", "PDF"))

    async def download(url):
        if url.endswith("/xmlarchive"):
            content = b'<?xml version="1.0" encoding="X-UNKNOWN"?><office-action/>'
        else:
            content = _word_document()
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["attempted_formats"] == ["XML", "MS_WORD"]


async def test_truncated_xml_tar_falls_back_to_word(monkeypatch, tmp_path):
    """Regression: a tar that opens but cannot be fully read must not abort fallback."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD", "PDF"))

    async def download(url):
        if url.endswith("/xmlarchive"):
            content = _truncated_xml_archive()
        else:
            content = _word_document()
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["attempted_formats"] == ["XML", "MS_WORD"]


async def test_malformed_word_container_falls_back_to_pdf(monkeypatch, tmp_path):
    """Regression: a mislabeled Word payload must not suppress the PDF fallback."""
    _install_listing(monkeypatch, _document_listing("MS_WORD", "PDF"))

    async def download(url):
        if url.endswith(".docx"):
            content = b"not an OOXML document"
            content_type = "application/octet-stream"
        else:
            content = b"%PDF-1.7 fallback"
            content_type = "application/pdf"
        return {
            "content": content,
            "content_type": content_type,
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "PDF"
    assert result["attempted_formats"] == ["MS_WORD", "PDF"]


async def test_corrupt_deflated_word_xml_falls_back_to_pdf(monkeypatch, tmp_path):
    """Regression: deflate errors inside OOXML must not abort PDF fallback."""
    _install_listing(monkeypatch, _document_listing("MS_WORD", "PDF"))

    async def download(url):
        content = (
            _corrupt_deflated_word_document()
            if url.endswith(".docx")
            else b"%PDF-1.7 fallback"
        )
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "PDF"
    assert result["attempted_formats"] == ["MS_WORD", "PDF"]


@pytest.mark.parametrize(
    "word_content",
    [_unsupported_compression_word_document, _oversized_word_document],
    ids=["unsupported-compression", "oversized-member"],
)
async def test_unsafe_word_archive_falls_back_to_pdf(
    monkeypatch, tmp_path, word_content
):
    """Regression: unsafe OOXML containers must become controlled fallbacks."""
    _install_listing(monkeypatch, _document_listing("MS_WORD", "PDF"))

    async def download(url):
        content = word_content() if url.endswith(".docx") else b"%PDF-1.7 fallback"
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "PDF"
    assert result["attempted_formats"] == ["MS_WORD", "PDF"]


async def test_xml_archive_stops_after_bounded_total_member_count(tmp_path):
    """Regression: non-XML tar members must count toward the archive bound."""
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for index in range(downloads.MAX_ARCHIVE_MEMBERS + 1):
            bundle.addfile(tarfile.TarInfo(f"padding/{index}.txt"))

    with pytest.raises(ValueError, match="too many total members"):
        downloads.save_downloaded_document(
            archive.getvalue(),
            "XML",
            f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/xmlarchive",
            str(tmp_path),
            APP_NUM,
            DOCUMENT_ID,
        )


async def test_raw_xml_with_utf8_bom_is_accepted(monkeypatch, tmp_path):
    """Regression: a standards-compliant XML BOM must not trigger PDF fallback."""
    _install_listing(monkeypatch, _document_listing("XML", "PDF"))

    async def download(url):
        content = b"\xef\xbb\xbf<office-action><claim>1</claim></office-action>"
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "XML"
    assert result["attempted_formats"] == ["XML"]


async def test_foreign_advertised_url_is_rejected_and_next_format_is_used(
    monkeypatch, tmp_path
):
    """Regression: a compromised listing must not turn the tool into an SSRF client."""
    listing = _document_listing("XML", "MS_WORD", "PDF")
    listing["documentBag"][0]["downloadOptionBag"][0]["downloadUrl"] = (
        f"https://evil.example.com/api/v1/download/applications/{APP_NUM}/"
        f"{DOCUMENT_ID}/xmlarchive"
    )
    _install_listing(monkeypatch, listing)

    async def download(url):
        if url.startswith("https://evil.example.com"):
            content = _xml_archive()
            return {
                "content": content,
                "content_type": "application/octet-stream",
                "size_bytes": len(content),
            }
        content = _word_document(b"safe-word")
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["fallback_used"] is True
    assert result["attempted_formats"] == ["XML", "MS_WORD"]
    with zipfile.ZipFile(result["file_path"]) as document:
        assert b"safe-word" in document.read("word/document.xml")


async def test_document_id_prefix_url_is_rejected_and_word_is_used(
    monkeypatch, tmp_path
):
    """Regression: the expected document ID must end at a URL path boundary."""
    listing = _document_listing("XML", "MS_WORD", "PDF")
    listing["documentBag"][0]["downloadOptionBag"][0]["downloadUrl"] = (
        f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}evil/xmlarchive"
    )
    _install_listing(monkeypatch, listing)

    async def download(url):
        if url.endswith("evil/xmlarchive"):
            content = _xml_archive()
        else:
            content = _word_document()
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "MS_WORD"
    assert result["attempted_formats"] == ["XML", "MS_WORD"]


async def test_duplicate_pdf_options_try_canonical_before_file_variant(
    monkeypatch, tmp_path
):
    """Regression: duplicate PDF options must retain the legacy canonical preference."""
    listing = _document_listing("PDF")
    options = listing["documentBag"][0]["downloadOptionBag"]
    options.insert(
        0,
        {
            "mimeTypeIdentifier": "PDF",
            "downloadUrl": (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                "uploaded.pdf"
            ),
        },
    )
    _install_listing(monkeypatch, listing)

    async def download(url):
        if url.endswith(f"/{DOCUMENT_ID}.pdf"):
            return {"error": True, "message": "canonical PDF unavailable"}
        content = b"%PDF-1.7 uploaded fallback"
        return {
            "content": content,
            "content_type": "application/pdf",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "PDF"
    assert result["attempted_formats"] == ["PDF", "PDF"]
    assert result["fallback_used"] is True


async def test_invalid_format_is_rejected_before_any_upstream_call(monkeypatch):
    """Regression: untrusted format input must not become download behavior."""

    async def tripwire(*args, **kwargs):
        raise AssertionError("invalid input reached the USPTO client")

    monkeypatch.setattr(patents.api_client, "make_request", tripwire)
    monkeypatch.setattr(patents.api_client, "download_file", tripwire)

    result = await patents.odp_download_document(
        APP_NUM, DOCUMENT_ID, preferred_format="HTML"
    )

    assert result["error"] is True
    assert result["error_code"] == "VALIDATION_ERROR"


async def test_path_like_document_id_is_rejected_before_upstream_call(monkeypatch):
    """Regression: document identifiers must never become filesystem paths."""

    async def tripwire(*args, **kwargs):
        raise AssertionError("unsafe document identifier reached the USPTO client")

    monkeypatch.setattr(patents.api_client, "make_request", tripwire)
    monkeypatch.setattr(patents.api_client, "download_file", tripwire)

    result = await patents.odp_download_document(APP_NUM, "../../outside")

    assert result["error"] is True
    assert result["error_code"] == "VALIDATION_ERROR"


async def test_null_download_option_bag_returns_controlled_error(monkeypatch):
    """Regression: a nullable upstream bag must not raise TypeError."""
    listing = _document_listing("XML")
    listing["documentBag"][0]["downloadOptionBag"] = None
    _install_listing(monkeypatch, listing)

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["error"] is True
    assert result["error_code"] == "INVALID_UPSTREAM_RESPONSE"


async def test_xml_archive_member_path_cannot_escape_download_directory(
    monkeypatch, tmp_path
):
    """Regression: a malicious archive member path must be reduced to a safe name."""
    _install_listing(monkeypatch, _document_listing("XML"))
    archive = _xml_archive("../../outside.xml")

    async def download(url):
        return {
            "content": archive,
            "content_type": "application/octet-stream",
            "size_bytes": len(archive),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path / "downloads"))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["selected_format"] == "XML"
    resolved_path = os.path.realpath(result["file_path"])
    assert os.path.basename(resolved_path) == "outside.xml"
    assert os.path.commonpath([resolved_path, str(tmp_path / "downloads")]) == str(
        tmp_path / "downloads"
    )
    assert not (tmp_path / "outside.xml").exists()
