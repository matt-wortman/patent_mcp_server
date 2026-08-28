"""Safe format selection and persistence for ODP file-wrapper documents."""

from __future__ import annotations

import io
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, Iterable, List
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree


DEFAULT_FORMAT_ORDER = ("XML", "MS_WORD", "PDF")
SUPPORTED_PREFERENCES = ("AUTO", *DEFAULT_FORMAT_ORDER)
MAX_EXTRACTED_XML_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_XML_FILES = 100
MAX_ARCHIVE_MEMBERS = 1000
MAX_NATIVE_MEMBER_BYTES = 10 * 1024 * 1024
MAX_NATIVE_COMPRESSION_RATIO = 200


def rank_download_options(
    options: Iterable[Dict[str, Any]],
    preferred_format: str,
) -> List[Dict[str, Any]]:
    """Return advertised download options in preferred, native-first order."""
    if preferred_format == "AUTO":
        order = DEFAULT_FORMAT_ORDER
    else:
        order = (preferred_format,) + tuple(
            item for item in DEFAULT_FORMAT_ORDER if item != preferred_format
        )

    rank = {format_name: index for index, format_name in enumerate(order)}
    usable = [
        option
        for option in options
        if isinstance(option, dict)
        and option.get("mimeTypeIdentifier") in rank
        and isinstance(option.get("downloadUrl"), str)
        and option["downloadUrl"].strip()
    ]
    return sorted(
        usable,
        key=lambda option: (
            rank[option["mimeTypeIdentifier"]],
            "/files/" in option["downloadUrl"]
            if option["mimeTypeIdentifier"] == "PDF"
            else False,
        ),
    )


def resolve_advertised_download_url(
    advertised_url: str,
    api_base_url: str,
    app_num: str,
    document_id: str,
) -> str:
    """Resolve an ODP option while rejecting foreign or unrelated URLs."""
    resolved = urljoin(f"{api_base_url.rstrip('/')}/", advertised_url)
    parsed = urlparse(resolved)
    base = urlparse(api_base_url)
    expected_prefix = f"/api/v1/download/applications/{app_num}/{document_id}"
    suffix = parsed.path[len(expected_prefix) :] if parsed.path.startswith(expected_prefix) else ""
    valid_document_path = (
        suffix == ".pdf"
        or suffix == "/xmlarchive"
        or (suffix.startswith("/files/") and len(suffix) > len("/files/"))
    )
    if (
        parsed.scheme != base.scheme
        or parsed.netloc != base.netloc
        or not valid_document_path
    ):
        raise ValueError("USPTO returned an invalid document download URL")
    return resolved


def save_downloaded_document(
    content: bytes,
    format_name: str,
    advertised_url: str,
    download_dir: str,
    app_num: str,
    document_id: str,
) -> Dict[str, Any]:
    """Persist a native or PDF document and return its readable file path."""
    os.makedirs(download_dir, exist_ok=True)
    if not isinstance(content, bytes) or not content:
        raise ValueError("USPTO returned empty or non-binary document content")
    if format_name == "XML":
        return _save_xml_content(content, download_dir, app_num, document_id)

    extension = ".pdf" if format_name == "PDF" else _word_extension(advertised_url)
    if format_name == "PDF":
        if b"%PDF-" not in content[:1024]:
            raise ValueError("USPTO PDF download does not contain a PDF header")
    else:
        _validate_word_content(content, extension)

    file_path = _write_private_file(
        content,
        download_dir,
        prefix=f"{app_num}_{document_id}_",
        suffix=extension,
    )
    return {"file_path": file_path, "extracted_file_paths": []}


def _save_xml_content(
    content: bytes,
    download_dir: str,
    app_num: str,
    document_id: str,
) -> Dict[str, Any]:
    try:
        _validate_xml(content)
    except ValueError:
        pass
    else:
        file_path = _write_private_file(
            content,
            download_dir,
            prefix=f"{app_num}_{document_id}_",
            suffix=".xml",
        )
        return {"file_path": file_path, "extracted_file_paths": [file_path]}

    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            xml_members = []
            for total_members, member in enumerate(archive, start=1):
                if total_members > MAX_ARCHIVE_MEMBERS:
                    raise ValueError(
                        "USPTO XML archive contains too many total members"
                    )
                if member.isfile() and member.name.lower().endswith(".xml"):
                    xml_members.append(member)
            if not xml_members:
                raise ValueError("USPTO XML archive contains no XML files")
            if len(xml_members) > MAX_EXTRACTED_XML_FILES:
                raise ValueError("USPTO XML archive contains too many XML files")

            extracted_bytes = sum(member.size for member in xml_members)
            if extracted_bytes > MAX_EXTRACTED_XML_BYTES:
                raise ValueError("USPTO XML archive exceeds the extraction size limit")

            validated_members = []
            used_names = set()
            for index, member in enumerate(xml_members, start=1):
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(
                        "USPTO XML archive contains an unreadable XML file"
                    )
                with source:
                    xml_content = source.read()
                _validate_xml(xml_content)
                filename = _safe_member_name(member.name, index, used_names)
                validated_members.append((filename, xml_content))
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("USPTO XML archive is not a readable tar archive") from exc

    output_dir = tempfile.mkdtemp(
        prefix=f"{app_num}_{document_id}_xml_",
        dir=download_dir,
    )
    extracted_paths: List[str] = []
    try:
        for filename, xml_content in validated_members:
            file_path = os.path.join(output_dir, filename)
            with open(file_path, "xb") as output:
                output.write(xml_content)
            extracted_paths.append(file_path)
    except OSError:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return {
        "file_path": extracted_paths[0],
        "extracted_file_paths": extracted_paths,
    }


def _safe_member_name(member_name: str, index: int, used_names: set[str]) -> str:
    basename = Path(unquote(member_name).replace("\\", "/")).name
    basename = re.sub(r"[^A-Za-z0-9._ -]", "_", basename).lstrip(".")
    if not basename or not basename.lower().endswith(".xml"):
        basename = f"document_{index}.xml"

    candidate = basename
    suffix = 2
    while candidate.lower() in used_names:
        stem, extension = os.path.splitext(basename)
        candidate = f"{stem}_{suffix}{extension}"
        suffix += 1
    used_names.add(candidate.lower())
    return candidate


def _word_extension(advertised_url: str) -> str:
    extension = os.path.splitext(unquote(urlparse(advertised_url).path))[1].lower()
    return extension if extension in {".doc", ".docx", ".docm"} else ".docx"


def _validate_xml(content: bytes) -> None:
    try:
        ElementTree.fromstring(content)
    except (ElementTree.ParseError, LookupError, ValueError) as exc:
        raise ValueError("USPTO native XML is not well formed") from exc


def _validate_word_content(content: bytes, extension: str) -> None:
    if extension == ".doc":
        if not content.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
            raise ValueError("USPTO Word download is not a valid legacy Word file")
        return

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as document:
            names = set(document.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("USPTO Word download is missing required OOXML files")
            document_info = document.getinfo("word/document.xml")
            if document_info.file_size > MAX_NATIVE_MEMBER_BYTES:
                raise ValueError("USPTO Word document XML exceeds the size limit")
            if (
                document_info.compress_size > 0
                and document_info.file_size / document_info.compress_size
                > MAX_NATIVE_COMPRESSION_RATIO
            ):
                raise ValueError("USPTO Word document XML is excessively compressed")
            if document_info.flag_bits & 0x1:
                raise ValueError("USPTO Word document XML is encrypted")
            _validate_xml(document.read(document_info))
    except ValueError:
        raise
    except (
        zipfile.BadZipFile,
        KeyError,
        NotImplementedError,
        RuntimeError,
        EOFError,
        zlib.error,
    ) as exc:
        raise ValueError("USPTO Word download is not a readable OOXML document") from exc


def _write_private_file(
    content: bytes,
    download_dir: str,
    *,
    prefix: str,
    suffix: str,
) -> str:
    descriptor, file_path = tempfile.mkstemp(
        prefix=prefix,
        suffix=suffix,
        dir=download_dir,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(file_path)
        except OSError:
            pass
        raise
    return file_path
