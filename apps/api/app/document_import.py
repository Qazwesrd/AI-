from pathlib import Path
import posixpath
from urllib.parse import unquote
from io import BytesIO
import zipfile
import zlib
import xml.etree.ElementTree as ET

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 50000
ARCHIVE_READ_CHUNK_BYTES = 1024 * 1024


def _verify_compressed_archive(archive):
    """Enforce ZIP limits using actual streamed output, not only central-directory metadata."""
    entries = archive.infolist()
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise ValueError("文档压缩包内文件数量过多，已拒绝解析")

    declared_total = 0
    for entry in entries:
        if entry.file_size < 0 or entry.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("文档压缩包包含超出单文件解压限制的内容")
        declared_total += entry.file_size
        if declared_total > MAX_ARCHIVE_EXPANDED_BYTES:
            raise ValueError("文档解压后超过 512 MB 安全限制，已拒绝解析")

    actual_total = 0
    try:
        for entry in entries:
            if entry.is_dir():
                continue
            actual_member = 0
            with archive.open(entry, "r") as source:
                while True:
                    chunk = source.read(ARCHIVE_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    actual_member += len(chunk)
                    actual_total += len(chunk)
                    if actual_member > MAX_ARCHIVE_MEMBER_BYTES:
                        raise ValueError("文档压缩包单个文件实际解压后超过 256 MB 安全限制")
                    if actual_total > MAX_ARCHIVE_EXPANDED_BYTES:
                        raise ValueError("文档压缩包实际解压总量超过 512 MB 安全限制")
            if actual_member != entry.file_size:
                raise ValueError("文档压缩包成员实际解压大小与声明不一致")
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError, zlib.error, NotImplementedError) as exc:
        raise ValueError("DOCX/EPUB 压缩包损坏或无法安全解压") from exc
    return actual_total


def validate_compressed_document(raw):
    """Reject ZIP-based documents whose declared and actual expansion exceeds safe limits."""
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            _verify_compressed_archive(archive)
    except zipfile.BadZipFile as exc:
        raise ValueError("DOCX/EPUB 文件不是有效的 ZIP 文档") from exc


def decode_text(raw):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("无法识别文本编码，请转换为 UTF-8")

def docx_text(raw):
    validate_compressed_document(raw)
    try:
        with zipfile.ZipFile(BytesIO(raw)) as z:
            xml = z.read("word/document.xml")
        root = ET.fromstring(xml)
    except KeyError as exc:
        raise ValueError("DOCX 文件缺少正文文档，无法导入") from exc
    except ET.ParseError as exc:
        raise ValueError("DOCX 正文 XML 损坏，无法导入") from exc
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    result = []
    for p in root.findall(".//w:p", ns):
        value = "".join(x.text or "" for x in p.findall(".//w:t", ns))
        if value.strip():
            result.append(value)
    return "\n".join(result)

def epub_text(raw):
    validate_compressed_document(raw)
    with zipfile.ZipFile(BytesIO(raw)) as z:
        archive_names = set(z.namelist())
        names = []

        # EPUB reading order is defined by the OPF spine, not ZIP entry order.
        # Follow container.xml -> package.opf -> manifest/spine when available.
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = container.find(
                ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
            )
            if rootfile is not None:
                package_path = rootfile.attrib.get("full-path", "")
                package = ET.fromstring(z.read(package_path))
                base_dir = posixpath.dirname(package_path)
                manifest = {}
                for item in package.findall(".//{*}manifest/{*}item"):
                    item_id = item.attrib.get("id")
                    href = item.attrib.get("href", "")
                    media_type = item.attrib.get("media-type", "")
                    if item_id and href and (
                        media_type in {"application/xhtml+xml", "text/html"}
                        or href.lower().split("?")[0].endswith((".xhtml", ".html", ".htm"))
                    ):
                        member = posixpath.normpath(
                            posixpath.join(base_dir, unquote(href.split("#", 1)[0].split("?", 1)[0]))
                        )
                        manifest[item_id] = member
                spine = package.find(".//{*}spine")
                if spine is not None:
                    for itemref in spine.findall("{*}itemref"):
                        member = manifest.get(itemref.attrib.get("idref", ""))
                        if member in archive_names and member not in names:
                            names.append(member)
        except (KeyError, ET.ParseError, OSError, ValueError):
            # Some legacy EPUBs omit or contain malformed package metadata.
            # Retain a best-effort fallback for those files.
            names = []

        if not names:
            names = [
                name for name in z.namelist()
                if name.lower().endswith((".xhtml", ".html", ".htm"))
            ]

        result = []
        for name in names:
            try:
                root = ET.fromstring(z.read(name))
                value = " ".join(x.strip() for x in root.itertext() if x.strip())
                if value:
                    result.append(value)
            except Exception:
                continue
    if not result:
        raise ValueError("EPUB 中没有读取到正文")
    return "\n\n".join(result)

def pdf_text(raw):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF 解析依赖未安装") from exc
    reader = PdfReader(BytesIO(raw))
    text = "\n\n".join(p.extract_text() or "" for p in reader.pages).strip()
    if not text:
        raise ValueError("PDF 没有可提取文字，可能是扫描图片版，当前未配置 OCR")
    return text

def rtf_text(raw):
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as exc:
        raise ValueError("RTF 解析依赖未安装") from exc
    return rtf_to_text(decode_text(raw))

def extract_document_text(filename, raw):
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("文件不能超过 1 GB")
    if not raw:
        raise ValueError("文件内容为空")
    ext = Path(filename or "").suffix.lower()
    if ext in (".txt", ".md", ".markdown", ".csv", ".log"):
        text = decode_text(raw)
    elif ext == ".docx":
        text = docx_text(raw)
    elif ext == ".epub":
        text = epub_text(raw)
    elif ext == ".pdf":
        text = pdf_text(raw)
    elif ext == ".rtf":
        text = rtf_text(raw)
    else:
        raise ValueError("支持 TXT、MD、DOCX、EPUB、PDF、RTF；旧版 DOC 请先转换为 DOCX")
    text = text.replace("\x00", "").strip()
    if not text:
        raise ValueError("没有读取到正文")
    return text
