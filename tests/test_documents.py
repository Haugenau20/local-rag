import importlib
import unittest


class DocumentTests(unittest.TestCase):
    def setUp(self):
        try:
            self.documents = importlib.import_module("local_rag.documents")
        except ModuleNotFoundError:
            self.fail("The document extraction and citation pipeline is not implemented")

    def test_utf8_bom_and_windows_newlines_keep_exact_line_locations(self):
        doc = self.documents.extract_text(b"\xef\xbb\xbfFirst\r\nSecond\r\n")
        self.assertEqual(doc.text, "First\nSecond\n")
        chunks = self.documents.chunk_document(doc, markdown=False, max_chars=100)
        self.assertEqual((chunks[0].start_line, chunks[0].end_line), (1, 2))
        self.assertEqual(chunks[0].text, doc.text[chunks[0].start_offset:chunks[0].end_offset])

    def test_repeated_passages_have_different_exact_locations(self):
        doc = self.documents.extract_text(b"same passage\n\nsame passage\n")
        chunks = self.documents.chunk_document(doc, markdown=False, max_chars=13)
        self.assertEqual([c.start_line for c in chunks], [1, 3])
        self.assertEqual([c.text for c in chunks], ["same passage", "same passage"])

    def test_markdown_sections_preserve_heading_hierarchy(self):
        doc = self.documents.extract_text(b"Intro\n\n# Main\nBody\n\n## Detail\nMore\n\n# Other\nEnd")
        chunks = self.documents.chunk_document(doc, markdown=True, max_chars=100)
        self.assertEqual([c.heading_path for c in chunks], [(), ("Main",), ("Main", "Detail"), ("Other",)])
        self.assertEqual([c.start_line for c in chunks], [1, 3, 6, 9])
        for c in chunks:
            self.assertEqual(c.text, doc.text[c.start_offset:c.end_offset])

    def test_heading_inside_fenced_code_is_not_a_section(self):
        doc = self.documents.extract_text(b"# Notes\n```python\n# example\n```\nText\n## Actual\nMore")
        chunks = self.documents.chunk_document(doc, markdown=True, max_chars=100)
        self.assertEqual([c.heading_path for c in chunks], [("Notes",), ("Notes", "Actual")])
        self.assertIn("# example", chunks[0].text)

    def test_long_line_is_bounded_and_every_excerpt_matches_its_offsets(self):
        doc = self.documents.extract_text(("x" * 99 + "\nend").encode())
        chunks = self.documents.chunk_document(doc, markdown=False, max_chars=16)
        self.assertTrue(all(len(c.text) <= 16 for c in chunks))
        self.assertEqual("".join(c.text for c in chunks).replace("\n", ""), "x" * 99 + "end")
        for c in chunks:
            self.assertEqual(c.text, doc.text[c.start_offset:c.end_offset])
            self.assertEqual(c.start_line, doc.text[:c.start_offset].count("\n") + 1)
            self.assertEqual(c.end_line, doc.text[:c.end_offset - 1].count("\n") + 1)

    def test_empty_or_whitespace_documents_have_no_chunks(self):
        for raw in (b"", b" \r\n\t"):
            self.assertEqual(self.documents.chunk_document(self.documents.extract_text(raw), markdown=False, max_chars=32), [])

    def test_invalid_utf8_is_reported_instead_of_silently_corrupted(self):
        with self.assertRaises(ValueError):
            self.documents.extract_text(b"\xff\xfeinvalid")

    def test_invalid_chunk_size_is_rejected(self):
        with self.assertRaises(ValueError):
            self.documents.chunk_document(self.documents.extract_text(b"text"), markdown=False, max_chars=0)

    def test_unicode_separator_does_not_invent_a_source_newline(self):
        doc = self.documents.extract_text("alpha\u2028beta\nnext".encode())
        chunks = self.documents.chunk_document(doc, markdown=False, max_chars=6)
        for chunk in chunks:
            self.assertEqual(chunk.start_line, doc.text[:chunk.start_offset].count("\n") + 1)
            self.assertEqual(chunk.end_line, doc.text[:chunk.end_offset - 1].count("\n") + 1)


if __name__ == "__main__":
    unittest.main()
