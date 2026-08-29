"""Behavior tests for native-text-first ODP file-wrapper downloads."""

import io
import os
import tarfile
import zipfile
from pathlib import Path

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


def _xml_archive_with_media() -> bytes:
    archive = io.BytesIO()
    members = {
        "wrapper/Non-Final Rejection.xml": (
            b"<office-action><image>media_image1.png</image></office-action>"
        ),
        "wrapper/media_image1.png": b"\x89PNG\r\n\x1a\nembedded-image",
    }
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for member_name, content in members.items():
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


async def test_xml_archive_retains_media_needed_by_the_selected_primary_record(
    tmp_path,
):
    """Regression: selecting XML must not discard image members from its archive."""
    saved = downloads.save_downloaded_document(
        _xml_archive_with_media(),
        "XML",
        f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/xmlarchive",
        str(tmp_path),
        APP_NUM,
        DOCUMENT_ID,
    )

    extracted_names = {
        os.path.basename(path): open(path, "rb").read()
        for path in saved["extracted_file_paths"]
    }
    assert extracted_names == {
        "Non-Final Rejection.xml": (
            b"<office-action><image>media_image1.png</image></office-action>"
        ),
        "media_image1.png": b"\x89PNG\r\n\x1a\nembedded-image",
    }


async def test_xml_archive_preserves_relative_media_paths(tmp_path):
    """Regression: XML-relative image references must still resolve after extraction."""
    archive = io.BytesIO()
    members = {
        "wrapper/Office Action.xml": (
            b"<office-action><image>images/page1.tif</image></office-action>"
        ),
        "wrapper/images/page1.tif": b"II*\x00nested-image",
        "wrapper/figures/page1.tif": b"II*\x00different-image",
    }
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for member_name, content in members.items():
            info = tarfile.TarInfo(member_name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))

    saved = downloads.save_downloaded_document(
        archive.getvalue(),
        "XML",
        f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/xmlarchive",
        str(tmp_path),
        APP_NUM,
        DOCUMENT_ID,
    )

    primary = Path(saved["file_path"])
    assert (primary.parent / "images" / "page1.tif").read_bytes() == (
        b"II*\x00nested-image"
    )
    assert (primary.parent / "figures" / "page1.tif").read_bytes() == (
        b"II*\x00different-image"
    )


async def test_xml_archive_preserves_safe_media_names_verbatim(tmp_path):
    """Regression: safe Unicode and percent characters in XML references must survive."""
    archive = io.BytesIO()
    media_name = "images/Figure(1)%20β.tif"
    members = {
        "wrapper/Office Action.xml": (
            f"<office-action><image>{media_name}</image></office-action>".encode()
        ),
        f"wrapper/{media_name}": b"II*\x00verbatim-name",
    }
    with tarfile.open(fileobj=archive, mode="w") as bundle:
        for member_name, content in members.items():
            info = tarfile.TarInfo(member_name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))

    saved = downloads.save_downloaded_document(
        archive.getvalue(),
        "XML",
        f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/xmlarchive",
        str(tmp_path),
        APP_NUM,
        DOCUMENT_ID,
    )

    primary = Path(saved["file_path"])
    assert (primary.parent / media_name).read_bytes() == b"II*\x00verbatim-name"


async def test_download_returns_one_best_primary_plus_all_unique_media(
    monkeypatch, tmp_path
):
    """Regression: native XML selection must not omit separately advertised images."""
    listing = _document_listing("XML", "PDF")
    listing["documentBag"][0]["downloadOptionBag"].extend(
        [
            {
                "mimeTypeIdentifier": "PNG",
                "downloadUrl": (
                    f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                    "media_image1.png"
                ),
                "pageTotalQuantity": 1,
            },
            {
                "mimeTypeIdentifier": "TIFF",
                "downloadUrl": (
                    f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                    "scan_2.tif"
                ),
                "pageTotalQuantity": 1,
            },
            {
                "mimeTypeIdentifier": "PNG",
                "downloadUrl": (
                    f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                    "duplicate_image.png"
                ),
                "pageTotalQuantity": 1,
            },
        ]
    )
    _install_listing(monkeypatch, listing)

    async def download(url):
        if url.endswith("/xmlarchive"):
            content = _xml_archive()
            content_type = "application/octet-stream"
        elif url.endswith("scan_2.tif"):
            content = b"II*\x00unique-tiff"
            content_type = "image/tiff"
        elif url.endswith(".png"):
            content = b"\x89PNG\r\n\x1a\nsame-image"
            content_type = "image/png"
        else:
            return {"error": True, "message": "redundant PDF requested"}
        return {
            "content": content,
            "content_type": content_type,
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["success"] is True
    assert result["complete"] is True
    assert result["selected_format"] == "XML"
    assert result["available_formats"] == ["PDF", "PNG", "TIFF", "XML"]
    assert result["attempted_formats"] == ["XML"]
    assert result["skipped_duplicate_count"] == 1
    assert {os.path.basename(path) for path in result["asset_file_paths"]} == {
        "media_image1.png",
        "scan_2.tif",
    }
    assert all(
        Path(path).parent == Path(result["file_path"]).parent
        for path in result["asset_file_paths"]
    )
    assert {
        (item["role"], item["format"], os.path.basename(item["file_path"]))
        for item in result["record_files"]
    } == {
        ("primary", "XML", os.path.basename(result["file_path"])),
        ("asset", "PNG", "media_image1.png"),
        ("asset", "TIFF", "scan_2.tif"),
    }
    assert all(len(item["sha256"]) == 64 for item in result["record_files"])


async def test_download_reports_incomplete_record_when_an_advertised_asset_fails(
    monkeypatch, tmp_path
):
    """Regression: a missing image must never be hidden behind primary-file success."""
    listing = _document_listing("XML", "PDF")
    listing["documentBag"][0]["downloadOptionBag"].append(
        {
            "mimeTypeIdentifier": "PNG",
            "downloadUrl": (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                "media_image1.png"
            ),
            "pageTotalQuantity": 1,
        }
    )
    _install_listing(monkeypatch, listing)

    async def download(url):
        if url.endswith("/xmlarchive"):
            content = _xml_archive()
            return {
                "content": content,
                "content_type": "application/octet-stream",
                "size_bytes": len(content),
            }
        return {"error": True, "message": "advertised image unavailable"}

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["error"] is True
    assert result["error_code"] == "INCOMPLETE_DOCUMENT_RECORD"
    assert result["details"]["complete"] is False
    assert os.path.isfile(result["details"]["file_path"])
    assert result["details"]["asset_errors"] == [
        {"format": "PNG", "message": "advertised image unavailable"}
    ]


@pytest.mark.parametrize(
    "malformed_option",
    [
        {"mimeTypeIdentifier": "PNG"},
        {
            "downloadUrl": (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                "media_image1.png"
            )
        },
        {"mimeTypeIdentifier": "PNG", "downloadUrl": ""},
        {"mimeTypeIdentifier": 123, "downloadUrl": ["not", "a", "url"]},
    ],
    ids=["missing-url", "missing-format", "blank-url", "wrong-types"],
)
async def test_malformed_advertised_option_is_never_reported_complete(
    monkeypatch, malformed_option
):
    """Regression: every advertised option must be retained or explicitly rejected."""
    listing = _document_listing("XML")
    listing["documentBag"][0]["downloadOptionBag"].append(malformed_option)
    _install_listing(monkeypatch, listing)

    async def tripwire(url):
        raise AssertionError("malformed options reached the download client")

    monkeypatch.setattr(patents.api_client, "download_file", tripwire)

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["error"] is True
    assert result["error_code"] == "INVALID_UPSTREAM_RESPONSE"


async def test_excessive_advertised_option_count_is_rejected(monkeypatch):
    """Regression: a malformed listing must not trigger unbounded downloads."""
    listing = _document_listing("XML")
    listing["documentBag"][0]["downloadOptionBag"].extend(
        {
            "mimeTypeIdentifier": "PNG",
            "downloadUrl": (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                f"image_{index}.png"
            ),
            "pageTotalQuantity": 1,
        }
        for index in range(101)
    )
    _install_listing(monkeypatch, listing)

    async def tripwire(url):
        raise AssertionError("excessive options reached the download client")

    monkeypatch.setattr(patents.api_client, "download_file", tripwire)

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["error"] is True
    assert result["error_code"] == "INVALID_UPSTREAM_RESPONSE"


async def test_image_only_record_keeps_each_unique_advertised_image(
    monkeypatch, tmp_path
):
    """Regression: a record with no XML, Word, or PDF must remain downloadable."""
    listing = {
        "documentBag": [
            {
                "applicationNumberText": APP_NUM,
                "directionCategory": "OUTGOING",
                "documentCode": "IMAGE",
                "documentCodeDescriptionText": "Image-only record",
                "documentIdentifier": DOCUMENT_ID,
                "officialDate": "2026-02-26T00:00:00.000-0500",
                "downloadOptionBag": [
                    {
                        "mimeTypeIdentifier": "PNG",
                        "downloadUrl": (
                            f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/"
                            "files/page_1.png"
                        ),
                        "pageTotalQuantity": 1,
                    },
                    {
                        "mimeTypeIdentifier": "PNG",
                        "downloadUrl": (
                            f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/"
                            "files/page_2.png"
                        ),
                        "pageTotalQuantity": 1,
                    },
                ],
            }
        ]
    }
    _install_listing(monkeypatch, listing)

    async def download(url):
        page = b"one" if url.endswith("page_1.png") else b"two"
        content = b"\x89PNG\r\n\x1a\n" + page
        return {
            "content": content,
            "content_type": "image/png",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["success"] is True
    assert result["complete"] is True
    assert result["selected_format"] == "PNG"
    assert result["ocr_may_be_required"] is True
    assert {item["role"] for item in result["record_files"]} == {
        "primary",
        "asset",
    }
    assert {os.path.basename(item["file_path"]) for item in result["record_files"]} == {
        "page_1.png",
        "page_2.png",
    }


@pytest.mark.parametrize(
    "encoded_name",
    [
        "%2e%2e%2fother-document.png",
        "%252e%252e%252fother-document.png",
        "..%255cother-document.png",
    ],
    ids=["single-encoded", "double-encoded-slash", "double-encoded-backslash"],
)
async def test_encoded_file_path_traversal_is_rejected(encoded_name):
    """Regression: an advertised media URL must stay within one document file."""
    with pytest.raises(ValueError, match="invalid document download URL"):
        downloads.resolve_advertised_download_url(
            (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                f"{encoded_name}"
            ),
            "https://api.uspto.gov",
            APP_NUM,
            DOCUMENT_ID,
        )


async def test_record_size_limit_reports_incomplete_media(monkeypatch, tmp_path):
    """Regression: ancillary files must not grow one record without a hard bound."""
    listing = _document_listing("XML")
    listing["documentBag"][0]["downloadOptionBag"].append(
        {
            "mimeTypeIdentifier": "PNG",
            "downloadUrl": (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                "large_image.png"
            ),
            "pageTotalQuantity": 1,
        }
    )
    _install_listing(monkeypatch, listing)

    async def download(url):
        content = _xml_archive() if url.endswith("/xmlarchive") else b"x" * 100
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(downloads, "MAX_COMPLETE_RECORD_BYTES", 80, raising=False)

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["error"] is True
    assert result["error_code"] == "INCOMPLETE_DOCUMENT_RECORD"
    assert "record size limit" in result["details"]["asset_errors"][0]["message"]


async def test_duplicate_asset_does_not_consume_aggregate_record_budget(
    monkeypatch, tmp_path
):
    """Regression: byte-identical media adds no retained bytes and must fit."""
    listing = _document_listing("XML")
    listing["documentBag"][0]["downloadOptionBag"].append(
        {
            "mimeTypeIdentifier": "PNG",
            "downloadUrl": (
                f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/files/"
                "duplicate.png"
            ),
            "pageTotalQuantity": 1,
        }
    )
    _install_listing(monkeypatch, listing)
    primary_content = b"<office-action><claim>1</claim></office-action>"

    async def download(url):
        content = _xml_archive() if url.endswith("/xmlarchive") else primary_content
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        downloads,
        "MAX_COMPLETE_RECORD_BYTES",
        len(primary_content),
    )

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["success"] is True
    assert result["complete"] is True
    assert result["skipped_duplicate_count"] == 1


async def test_asset_manifest_failure_removes_unreported_file(monkeypatch, tmp_path):
    """Regression: a post-write hashing failure must not leave an orphaned asset."""
    known_hashes = {}

    def fail_manifest(*args, **kwargs):
        raise OSError("hashing failed")

    monkeypatch.setattr(downloads, "build_file_manifest", fail_manifest)

    with pytest.raises(OSError, match="hashing failed"):
        downloads.save_unique_asset(
            b"asset-content",
            "PNG",
            "/files/media_image1.png",
            "image/png",
            str(tmp_path),
            known_hashes,
        )

    assert list(tmp_path.iterdir()) == []
    assert known_hashes == {}


async def test_primary_manifest_failure_removes_abandoned_fallback_directory(
    monkeypatch, tmp_path
):
    """Regression: a failed primary manifest must not leave an unreported record."""
    _install_listing(monkeypatch, _document_listing("XML", "MS_WORD"))

    async def download(url):
        content = _xml_archive() if url.endswith("/xmlarchive") else _word_document()
        return {
            "content": content,
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
        }

    real_manifest = patents.manifest_for_saved_document

    def fail_xml_manifest(saved, format_name, content_type):
        if format_name == "XML":
            raise OSError("hashing failed")
        return real_manifest(saved, format_name, content_type)

    monkeypatch.setattr(patents.api_client, "download_file", download)
    monkeypatch.setattr(patents, "manifest_for_saved_document", fail_xml_manifest)
    monkeypatch.setattr(patents.config, "DOWNLOAD_DIR", str(tmp_path))

    result = await patents.odp_download_document(APP_NUM, DOCUMENT_ID)

    assert result["success"] is True
    assert result["selected_format"] == "MS_WORD"
    assert [path for path in tmp_path.iterdir() if path.is_dir()] == [
        Path(result["file_path"]).parent
    ]


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
    tmp_path,
):
    """Regression: a malicious archive member path must be rejected."""
    archive = _xml_archive("../../outside.xml")

    with pytest.raises(ValueError, match="unsafe member path"):
        downloads.save_downloaded_document(
            archive,
            "XML",
            f"/api/v1/download/applications/{APP_NUM}/{DOCUMENT_ID}/xmlarchive",
            str(tmp_path / "downloads"),
            APP_NUM,
            DOCUMENT_ID,
        )
    assert not (tmp_path / "outside.xml").exists()
