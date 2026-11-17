from pathlib import Path
from io import BytesIO
import zipfile
import xml.etree.ElementTree as ET

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024

def decode_text(raw):
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("无法识别文本编码，请转换为 UTF-8")

def docx_text(raw):
    with zipfile.ZipFile(BytesIO(raw)) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    result = []
    for p in root.findall(".//w:p", ns):
        value = "".join(x.text or "" for x in p.findall(".//w:t", ns))
        if value.strip():
            result.append(value)
    return "\n".join(result)

def epub_text(raw):
    with zipfile.ZipFile(BytesIO(raw)) as z:
        names = [n for n in z.namelist()
                 if n.lower().endswith((".xhtml", ".html", ".htm"))]
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
