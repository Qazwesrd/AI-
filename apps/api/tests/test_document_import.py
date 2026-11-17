import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from app.document_import import decode_text, docx_text, epub_text, extract_document_text
from app.routers.novels import split_chapters


class DocumentImportTests(unittest.TestCase):
    def test_decode_utf8_bom(self):
        self.assertEqual(decode_text("\\ufeff第一章\\n正文".encode("utf-8")), "第一章\\n正文")

    def test_decode_gb18030(self):
        text = "第一章\\n这是中文正文"
        self.assertEqual(decode_text(text.encode("gb18030")), text)

    def test_split_chinese_chapters(self):
        source = "序章\\n开头\\n第1章 初遇\\n第一段\\n第2章 再见\\n第二段"
        chapters = split_chapters(source)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0][0], "序章")
        self.assertIn("第一段", chapters[1][1])
        self.assertIn("第二段", chapters[2][1])

    def test_split_english_chapters(self):
        chapters = split_chapters("Chapter 1: Start\\nHello\\nChapter 2: End\\nBye")
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

    def test_epub_extraction(self):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("OEBPS/chapter1.xhtml", "<html><body><p>EPUB正文</p></body></html>")
        self.assertIn("EPUB正文", epub_text(buffer.getvalue()))

    def test_txt_document_import(self):
        self.assertEqual(
            extract_document_text("novel.txt", "正文内容".encode("utf-8")),
            "正文内容",
        )

    def test_reject_unsupported_format(self):
        with self.assertRaises(ValueError):
            extract_document_text("novel.doc", b"content")

    def test_reject_empty_file(self):
        with self.assertRaises(ValueError):
            extract_document_text("novel.txt", b"")


if __name__ == "__main__":
    unittest.main()
