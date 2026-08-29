"""Safe format selection and persistence for ODP file-wrapper documents."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
import shutil
import tarfile
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import unquote, urljoin, urlparse
from xml.etree import ElementTree


DEFAULT_FORMAT_ORDER = ("XML", "MS_WORD", "PDF")
SUPPORTED_PREFERENCES = ("AUTO", *DEFAULT_FORMAT_ORDER)
MAX_EXTRACTED_XML_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_RECORD_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_XML_FILES = 100
MAX_ARCHIVE_MEMBERS = 1000
MAX_NATIVE_MEMBER_BYTES = 10 * 1024 * 1024
MAX_NATIVE_COMPRESSION_RATIO = 200
MAX_DOWNLOAD_OPTIONS = 100
MAX_COMPLETE_RECORD_BYTES = 250 * 1024 * 1024
MAX_URL_DECODE_PASSES = 5


def validate_download_options(options: List[Dict[str, Any]]) -> None:
    """Reject malformed or excessive advertised options before any download."""
    if len(options) > MAX_DOWNLOAD_OPTIONS:
        raise ValueError(
            f"USPTO document listing exceeds the {MAX_DOWNLOAD_OPTIONS}-option limit"
        )
    for index, option in enumerate(options, start=1):
        format_name = option.get("mimeTypeIdentifier")
        advertised_url = option.get("downloadUrl")
        if not isinstance(format_name, str) or not format_name.strip():
            raise ValueError(
                f"USPTO download option {index} has an invalid mimeTypeIdentifier"
            )
        if not isinstance(advertised_url, str) or not advertised_url.strip():
            raise ValueError(
                f"USPTO download option {index} has an invalid downloadUrl"
            )


def validate_record_growth(current_bytes: int, additional_bytes: int) -> None:
    """Bound the aggregate persisted size of one complete record."""
    if current_bytes < 0 or additional_bytes < 0:
        raise ValueError("USPTO returned an invalid asset size")
    if current_bytes + additional_bytes > MAX_COMPLETE_RECORD_BYTES:
        raise ValueError(
            f"USPTO asset would exceed the {MAX_COMPLETE_RECORD_BYTES}-byte "
            "complete-record size limit"
        )


def asset_sha256(content: bytes) -> str:
    """Hash a non-empty advertised asset before dedupe or persistence."""
    if not isinstance(content, bytes) or not content:
        raise ValueError("USPTO returned empty or non-binary asset content")
    return hashlib.sha256(content).hexdigest()


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


def ancillary_download_options(
    options: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return unique advertised files that are not competing primary formats."""
    ancillary = []
    seen_urls = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        format_name = option.get("mimeTypeIdentifier")
        advertised_url = option.get("downloadUrl")
        if (
            format_name in DEFAULT_FORMAT_ORDER
            or not isinstance(format_name, str)
            or not format_name.strip()
            or not isinstance(advertised_url, str)
            or not advertised_url.strip()
            or advertised_url in seen_urls
        ):
            continue
        seen_urls.add(advertised_url)
        ancillary.append(option)
    return ancillary


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
    encoded_file_name = suffix[len("/files/") :] if suffix.startswith("/files/") else ""
    safe_file_name = _is_safe_advertised_file_name(encoded_file_name)
    valid_document_path = (
        suffix == ".pdf"
        or suffix == "/xmlarchive"
        or (
            bool(encoded_file_name)
            and safe_file_name
        )
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
    content_type: str = "application/octet-stream",
) -> Dict[str, Any]:
    """Persist a native or PDF document and return its readable file path."""
    os.makedirs(download_dir, exist_ok=True)
    if not isinstance(content, bytes) or not content:
        raise ValueError("USPTO returned empty or non-binary document content")
    if format_name == "XML":
        return _save_xml_content(content, download_dir, app_num, document_id)
    if format_name not in DEFAULT_FORMAT_ORDER:
        record_dir = tempfile.mkdtemp(
            prefix=f"{app_num}_{document_id}_record_",
            dir=download_dir,
        )
        try:
            persisted = save_unique_asset(
                content,
                format_name,
                advertised_url,
                content_type,
                record_dir,
                {},
            )
        except (OSError, ValueError):
            shutil.rmtree(record_dir, ignore_errors=True)
            raise
        file_path = persisted["manifest"]["file_path"]
        return {
            "file_path": file_path,
            "extracted_file_paths": [],
            "record_dir": record_dir,
        }

    extension = ".pdf" if format_name == "PDF" else _word_extension(advertised_url)
    if format_name == "PDF":
        if b"%PDF-" not in content[:1024]:
            raise ValueError("USPTO PDF download does not contain a PDF header")
    else:
        _validate_word_content(content, extension)

    record_dir = tempfile.mkdtemp(
        prefix=f"{app_num}_{document_id}_record_",
        dir=download_dir,
    )
    try:
        file_path = _write_private_file(
            content,
            record_dir,
            prefix=f"{app_num}_{document_id}_",
            suffix=extension,
        )
    except OSError:
        shutil.rmtree(record_dir, ignore_errors=True)
        raise
    return {
        "file_path": file_path,
        "extracted_file_paths": [],
        "record_dir": record_dir,
    }


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
        record_dir = tempfile.mkdtemp(
            prefix=f"{app_num}_{document_id}_record_",
            dir=download_dir,
        )
        try:
            file_path = _write_private_file(
                content,
                record_dir,
                prefix=f"{app_num}_{document_id}_",
                suffix=".xml",
            )
        except OSError:
            shutil.rmtree(record_dir, ignore_errors=True)
            raise
        return {
            "file_path": file_path,
            "extracted_file_paths": [file_path],
            "record_dir": record_dir,
        }

    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:*") as archive:
            xml_members = []
            file_members = []
            for total_members, member in enumerate(archive, start=1):
                if total_members > MAX_ARCHIVE_MEMBERS:
                    raise ValueError(
                        "USPTO XML archive contains too many total members"
                    )
                if member.isfile():
                    file_members.append(member)
                    if member.name.lower().endswith(".xml"):
                        xml_members.append(member)
            if not xml_members:
                raise ValueError("USPTO XML archive contains no XML files")
            if len(xml_members) > MAX_EXTRACTED_XML_FILES:
                raise ValueError("USPTO XML archive contains too many XML files")

            extracted_bytes = sum(member.size for member in xml_members)
            if extracted_bytes > MAX_EXTRACTED_XML_BYTES:
                raise ValueError("USPTO XML archive exceeds the extraction size limit")
            record_bytes = sum(member.size for member in file_members)
            if record_bytes > MAX_EXTRACTED_RECORD_BYTES:
                raise ValueError("USPTO XML archive record exceeds the extraction size limit")

            validated_members = []
            used_paths = set()
            for index, member in enumerate(file_members, start=1):
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(
                        "USPTO XML archive contains an unreadable file"
                    )
                with source:
                    member_content = source.read()
                if member.name.lower().endswith(".xml"):
                    _validate_xml(member_content)
                relative_path = _safe_member_path(member.name)
                normalized_path = relative_path.lower()
                if normalized_path in used_paths:
                    raise ValueError(
                        "USPTO XML archive contains a duplicate member path"
                    )
                used_paths.add(normalized_path)
                validated_members.append((relative_path, member_content))
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError("USPTO XML archive is not a readable tar archive") from exc

    output_dir = tempfile.mkdtemp(
        prefix=f"{app_num}_{document_id}_xml_",
        dir=download_dir,
    )
    extracted_paths: List[str] = []
    output_root = os.path.realpath(output_dir)
    try:
        for relative_path, member_content in validated_members:
            file_path = os.path.realpath(os.path.join(output_dir, relative_path))
            if os.path.commonpath([file_path, output_root]) != output_root:
                raise ValueError("USPTO XML archive contains an unsafe member path")
            os.makedirs(os.path.dirname(file_path), mode=0o700, exist_ok=True)
            with open(file_path, "xb") as output:
                output.write(member_content)
            extracted_paths.append(file_path)
    except OSError:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return {
        "file_path": next(
            path for path in extracted_paths if path.lower().endswith(".xml")
        ),
        "extracted_file_paths": extracted_paths,
        "record_dir": output_dir,
    }


def build_file_manifest(
    file_path: str,
    *,
    role: str,
    format_name: str,
    source: str,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Describe one persisted record file with an integrity hash."""
    digest = hashlib.sha256()
    size_bytes = 0
    with open(file_path, "rb") as source_file:
        while chunk := source_file.read(1024 * 1024):
            digest.update(chunk)
            size_bytes += len(chunk)
    detected_type = mimetypes.guess_type(file_path)[0]
    return {
        "role": role,
        "format": format_name,
        "source": source,
        "file_path": file_path,
        "content_type": content_type or detected_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "sha256": digest.hexdigest(),
    }


def manifest_for_saved_document(
    saved: Dict[str, Any],
    format_name: str,
    content_type: str,
) -> List[Dict[str, Any]]:
    """Build primary/asset manifest entries for a saved selected representation."""
    paths = saved.get("extracted_file_paths") or [saved["file_path"]]
    manifest = []
    for path in paths:
        is_primary = path == saved["file_path"]
        manifest.append(
            build_file_manifest(
                path,
                role="primary" if is_primary else "asset",
                format_name=(
                    format_name if is_primary else _format_from_path(path)
                ),
                source="primary_download" if is_primary else "primary_archive",
                content_type=(
                    None if format_name == "XML" else content_type
                ) if is_primary else None,
            )
        )
    return manifest


def save_unique_asset(
    content: bytes,
    format_name: str,
    advertised_url: str,
    content_type: str,
    record_dir: str,
    known_hashes: Dict[str, str],
) -> Dict[str, Any]:
    """Persist one ancillary file unless identical bytes already exist."""
    digest = asset_sha256(content)
    if digest in known_hashes:
        return {
            "duplicate": True,
            "duplicate_of": known_hashes[digest],
            "sha256": digest,
        }

    filename = _safe_asset_name(advertised_url, format_name, content_type)
    file_path = _unique_path(record_dir, filename)
    descriptor = os.open(file_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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

    known_hashes[digest] = file_path
    try:
        manifest = build_file_manifest(
            file_path,
            role="asset",
            format_name=format_name,
            source="advertised_asset",
            content_type=content_type,
        )
    except (OSError, ValueError):
        known_hashes.pop(digest, None)
        try:
            os.unlink(file_path)
        except OSError:
            pass
        raise
    return {"duplicate": False, "manifest": manifest}


def _safe_member_path(member_name: str) -> str:
    if (
        not member_name
        or "\\" in member_name
        or any(ord(character) < 32 or ord(character) == 127 for character in member_name)
    ):
        raise ValueError("USPTO XML archive contains an unsafe member path")
    path = PurePosixPath(member_name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or re.fullmatch(r"[A-Za-z]:.*", path.parts[0]) is not None
    ):
        raise ValueError("USPTO XML archive contains an unsafe member path")
    return os.path.join(*path.parts)


def _decode_stable(value: str) -> str:
    current = value
    for _ in range(MAX_URL_DECODE_PASSES):
        decoded = unquote(current)
        if decoded == current:
            return current
        current = decoded
    if unquote(current) != current:
        raise ValueError("USPTO path contains excessive encoding")
    return current


def _is_safe_advertised_file_name(encoded_file_name: str) -> bool:
    try:
        decoded = _decode_stable(encoded_file_name)
    except ValueError:
        return False
    return (
        bool(decoded)
        and decoded not in {".", ".."}
        and "/" not in decoded
        and "\\" not in decoded
        and not any(ord(character) < 32 for character in decoded)
    )


def _safe_asset_name(
    advertised_url: str,
    format_name: str,
    content_type: str,
) -> str:
    basename = Path(unquote(urlparse(advertised_url).path).replace("\\", "/")).name
    basename = re.sub(r"[^A-Za-z0-9._ -]", "_", basename).lstrip(".")
    if basename:
        return basename

    media_type = content_type.split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(media_type) or ".bin"
    safe_format = re.sub(r"[^A-Za-z0-9_-]", "_", format_name).strip("_").lower()
    return f"asset_{safe_format or 'file'}{extension}"


def _unique_path(directory: str, filename: str) -> str:
    candidate = os.path.join(directory, filename)
    stem, extension = os.path.splitext(filename)
    suffix = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{stem}_{suffix}{extension}")
        suffix += 1
    return candidate


def _format_from_path(file_path: str) -> str:
    extension = os.path.splitext(file_path)[1].lower()
    aliases = {".tif": "TIFF", ".tiff": "TIFF", ".jpg": "JPEG", ".jpeg": "JPEG"}
    return aliases.get(extension, extension.lstrip(".").upper() or "BINARY")


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
