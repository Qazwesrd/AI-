import unittest
import zipfile
from io import BytesIO
from unittest.mock import MagicMock, patch

from app.document_import import (
    _verify_compressed_archive, decode_text, docx_text, epub_text, extract_document_text,
)
from app.routers.novels import split_chapters


class DocumentImportTests(unittest.TestCase):
    def test_decode_utf8_bom(self):
        self.assertEqual(decode_text("\ufeff第一章\n正文".encode("utf-8")), "第一章\n正文")

    def test_decode_gb18030(self):
        text = "第一章\n这是中文正文"
        self.assertEqual(decode_text(text.encode("gb18030")), text)

    def test_split_chinese_chapters(self):
        source = "序章\n开头\n第1章 初遇\n第一段\n第2章 再见\n第二段"
        chapters = split_chapters(source)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0][0], "序章")
        self.assertIn("第一段", chapters[1][1])
        self.assertIn("第二段", chapters[2][1])

    def test_split_english_chapters(self):
        chapters = split_chapters("Chapter 1: Start\nHello\nChapter 2: End\nBye")
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0][0], "Chapter 1: Start")
        self.assertEqual(chapters[1][0], "Chapter 2: End")

    def test_docx_extraction(self):
        buffer = BytesIO()
        document_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>测试文档正文</w:t></w:r></w:p></w:body></w:document>'
        )
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        self.assertIn("测试文档正文", docx_text(buffer.getvalue()))

    def test_docx_extraction_from_seekable_stream(self):
        buffer = BytesIO()
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>流式 DOCX 正文</w:t></w:r></w:p></w:body></w:document>'
        )
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", document_xml)
        self.assertIn("流式 DOCX 正文", docx_text(BytesIO(buffer.getvalue())))

    def test_epub_extraction(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("OEBPS/chapter1.xhtml", "<html><body><p>EPUB正文</p></body></html>")
        self.assertIn("EPUB正文", epub_text(buffer.getvalue()))

    def test_epub_uses_spine_reading_order_not_zip_order(self):
        buffer = BytesIO()
        container_xml = (
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OPS/package.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        )
        package_xml = (
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<manifest>'
            '<item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="chapter1"/><itemref idref="chapter2"/></spine>'
            '</package>'
        )
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("OPS/chapter2.xhtml", "<html><body>第二章</body></html>")
            archive.writestr("META-INF/container.xml", container_xml)
            archive.writestr("OPS/package.opf", package_xml)
            archive.writestr("OPS/chapter1.xhtml", "<html><body>第一章</body></html>")

        extracted = epub_text(buffer.getvalue())
        self.assertLess(extracted.index("第一章"), extracted.index("第二章"))

    def test_docx_rejects_archive_expansion_over_limit(self):
        buffer = BytesIO()
        document_xml = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>' + ("正文" * 100) + '</w:t></w:r></w:p></w:body></w:document>'
        )
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", document_xml)
        with patch("app.document_import.MAX_ARCHIVE_EXPANDED_BYTES", 20):
            with self.assertRaisesRegex(ValueError, "解压后超过"):
                docx_text(buffer.getvalue())

    def test_epub_rejects_too_many_archive_entries(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("chapter1.xhtml", "<p>第一章</p>")
            archive.writestr("chapter2.xhtml", "<p>第二章</p>")
        with patch("app.document_import.MAX_ARCHIVE_ENTRIES", 1):
            with self.assertRaisesRegex(ValueError, "文件数量过多"):
                epub_text(buffer.getvalue())

    def test_docx_rejects_oversized_single_member(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("word/document.xml", "<p>" + ("x" * 100) + "</p>")
        with patch("app.document_import.MAX_ARCHIVE_MEMBER_BYTES", 50):
            with self.assertRaisesRegex(ValueError, "单文件解压限制"):
                docx_text(buffer.getvalue())

    def test_streamed_validation_enforces_actual_single_member_limit(self):
        entry = MagicMock()
        entry.is_dir.return_value = False
        entry.file_size = 5
        archive = MagicMock()
        archive.infolist.return_value = [entry]
        archive.open.return_value = BytesIO(b"x" * 20)
        with patch("app.document_import.MAX_ARCHIVE_MEMBER_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "实际解压"):
                _verify_compressed_archive(archive)

    def test_streamed_validation_rejects_size_metadata_mismatch(self):
        entry = MagicMock()
        entry.is_dir.return_value = False
        entry.file_size = 5
        archive = MagicMock()
        archive.infolist.return_value = [entry]
        archive.open.return_value = BytesIO(b"valid output")
        with self.assertRaisesRegex(ValueError, "大小与声明不一致"):
            _verify_compressed_archive(archive)


    def test_text_import_accepts_seekable_stream(self):
        source = BytesIO("第一章\n正文内容".encode("utf-8"))
        self.assertEqual(
            extract_document_text("novel.txt", source),
            "第一章\n正文内容",
        )

    def test_stream_text_import_supports_gb18030(self):
        expected = "第一章\n中文正文"
        source = BytesIO(expected.encode("gb18030"))
        self.assertEqual(extract_document_text("novel.txt", source), expected)

    def test_stream_import_rejects_oversized_upload(self):
        source = BytesIO(b"123456")
        with patch("app.document_import.MAX_UPLOAD_BYTES", 5):
            with self.assertRaisesRegex(ValueError, "不能超过 1 GB"):
                extract_document_text("novel.txt", source)

    def test_txt_document_import(self):
        self.assertEqual(extract_document_text("novel.txt", "正文内容".encode("utf-8")), "正文内容")

    def test_rtf_document_import(self):
        raw = br"{\rtf1\ansi 第一章\par RTF 正文}"
        self.assertIn("RTF 正文", extract_document_text("novel.rtf", raw))

    def test_pdf_without_text_is_rejected_with_clear_message(self):
        from pypdf import PdfWriter
        buffer = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=400)
        writer.write(buffer)
        with self.assertRaisesRegex(ValueError, "没有可提取文字"):
            extract_document_text("novel.pdf", buffer.getvalue())

    def test_reject_unsupported_format(self):
        with self.assertRaises(ValueError):
            extract_document_text("novel.doc", b"content")

    def test_reject_empty_file(self):
        with self.assertRaises(ValueError):
            extract_document_text("novel.txt", b"")


if __name__ == "__main__":
    unittest.main()
