"""Tests for :mod:`pkb.sources` — extraction and staging (LS-7, LS-8, LS-9, LS-10).

Every fixture is generated in ``tmp_path``: small PDFs are assembled byte by byte, small EPUBs are
zipped by hand in both layouts, and the one URL test injects a fetcher. Nothing here touches the
network or needs an API key.

The hostile cases carry the weight. A scanned PDF, a blank PDF, an encrypted PDF and a PDF whose
fonts carry no character map all "succeed" in every extraction library there is — they return the
empty string. The tests below assert that each one raises, and raises *its own* error, because a
caller that cannot tell them apart cannot decide what to do next and will default to treating
silence as a finished reading.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfWriter

from pkb.core.scaffold import scaffold_topic
from pkb.core.scan import scan
from pkb.core.validation import validate_tree
from pkb.sources import (
    INBOX_DIR,
    EmptySourceError,
    EncryptedSourceError,
    ExtractedSource,
    Fetched,
    FetchError,
    GarbledTextError,
    MalformedSourceError,
    ScannedSourceError,
    Section,
    SourceNotFoundError,
    StructureMethod,
    UnsupportedSourceError,
    check_fetchable,
    detect_kind,
    extract,
    find_staged,
    inbox_root,
    load_extraction,
    render_markdown,
    save_extraction,
    stage,
)

# --------------------------------------------------------------------------------------
# PDF fixtures — raw bytes, so no writer's private API and no extra dependency
# --------------------------------------------------------------------------------------

Run = tuple[str, float, float]
"""One drawn string: (text, font size, baseline y)."""

_TOUNICODE_TO_PUA = """/CIDInit /ProcSet findresource begin
12 dict begin
begincmap
/CMapName /Garbled def
/CMapType 2 def
1 begincodespacerange
<20> <7e>
endcodespacerange
1 beginbfrange
<20> <7e> <e020>
endbfrange
endcmap
CMapName currentdict /CMap defineresource pop
end
end"""
"""Maps every printable byte into the Private Use Area — a font with no usable character map."""


def make_pdf(
    pages: Sequence[Sequence[Run]],
    *,
    with_image: bool = False,
    title: str | None = None,
    garbled: bool = False,
) -> bytes:
    """A minimal but genuinely valid PDF: catalog, page tree, one Type1 font, one stream per page."""
    objects: dict[int, bytes] = {}
    catalog_id, pages_id, font_id = 1, 2, 3
    next_id = 4
    image_id = None
    if with_image:
        image_id, next_id = next_id, next_id + 1
    tounicode_id = None
    if garbled:
        tounicode_id, next_id = next_id, next_id + 1
    page_ids: list[int] = []
    content_ids: list[int] = []
    for _ in pages:
        page_ids.append(next_id)
        content_ids.append(next_id + 1)
        next_id += 2
    info_id = None
    if title:
        info_id, next_id = next_id, next_id + 1

    objects[catalog_id] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
        "latin-1"
    )

    font = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
    if tounicode_id:
        font += f" /ToUnicode {tounicode_id} 0 R"
    objects[font_id] = (font + " >>").encode("latin-1")

    if tounicode_id:
        objects[tounicode_id] = _stream(_TOUNICODE_TO_PUA.encode("latin-1"))
    if image_id:
        objects[image_id] = _stream(
            b"\x00",
            "/Type /XObject /Subtype /Image /Width 1 /Height 1 /ColorSpace /DeviceGray "
            "/BitsPerComponent 8",
        )

    for index, runs in enumerate(pages):
        ops: list[str] = []
        if image_id:
            ops.append("q 200 0 0 200 72 400 cm /Im1 Do Q")
        for text, size, y in runs:
            escaped = text.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
            ops.append(f"BT /F1 {size} Tf 72 {y} Td ({escaped}) Tj ET")
        objects[content_ids[index]] = _stream("\n".join(ops).encode("latin-1"))
        xobject = f" /XObject << /Im1 {image_id} 0 R >>" if image_id else ""
        objects[page_ids[index]] = (
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >>{xobject} >> "
            f"/Contents {content_ids[index]} 0 R >>"
        ).encode("latin-1")
    if info_id:
        objects[info_id] = f"<< /Title ({title}) >>".encode("latin-1")

    return _assemble_pdf(objects, catalog_id, info_id)


def _stream(payload: bytes, extra: str = "") -> bytes:
    header = f"<< {extra} /Length {len(payload)} >>".replace("<<  ", "<< ")
    return header.encode("latin-1") + b"\nstream\n" + payload + b"\nendstream"


def _assemble_pdf(objects: dict[int, bytes], catalog_id: int, info_id: int | None) -> bytes:
    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode("latin-1") + objects[number] + b"\nendobj\n"
    xref_at = len(out)
    count = max(objects) + 1
    out += f"xref\n0 {count}\n".encode("latin-1") + b"0000000000 65535 f \n"
    for number in range(1, count):
        out += f"{offsets.get(number, 0):010d} 00000 n \n".encode("latin-1")
    trailer = f"<< /Size {count} /Root {catalog_id} 0 R"
    if info_id:
        trailer += f" /Info {info_id} 0 R"
    out += b"trailer\n" + (trailer + " >>").encode("latin-1")
    out += f"\nstartxref\n{xref_at}\n%%EOF\n".encode("latin-1")
    return bytes(out)


def add_outline(raw: bytes, entries: Sequence[tuple[str, int, int]]) -> bytes:
    """Attach a bookmark tree. ``entries`` are ``(title, level, page index)``, parents first."""
    writer = PdfWriter(clone_from=io.BytesIO(raw))
    parents: dict[int, object] = {}
    for title, level, page in entries:
        parent = parents.get(level - 1) if level > 1 else None
        parents[level] = writer.add_outline_item(title, page, parent=parent)  # type: ignore[arg-type]
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


BOOK_PAGES: list[list[Run]] = [
    [("A preface, written before the chapters begin.", 11.0, 700.0)],
    [
        ("Challenge directly", 24.0, 700.0),
        ("Say the thing that is true and hard.", 11.0, 660.0),
        ("Say it once", 14.0, 620.0),
        ("Repeating it is punishment, not clarity.", 11.0, 590.0),
    ],
    [("Care personally", 24.0, 700.0), ("Mean it, or the first half is cruelty.", 11.0, 660.0)],
]


@pytest.fixture
def outlined_pdf(tmp_path: Path) -> Path:
    raw = make_pdf(BOOK_PAGES, title="Radical Candor")
    path = tmp_path / "radical-candor.pdf"
    path.write_bytes(
        add_outline(
            raw,
            [("Challenge directly", 1, 1), ("Say it once", 2, 1), ("Care personally", 1, 2)],
        )
    )
    return path


# --------------------------------------------------------------------------------------
# EPUB fixtures — both layouts
# --------------------------------------------------------------------------------------

CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""


def write_epub(path: Path, entries: dict[str, str]) -> Path:
    """Zip an EPUB, with ``mimetype`` first and stored, exactly as the format requires."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER_XML)
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


def _chapter_xhtml(title: str, paragraphs: Sequence[str]) -> str:
    body = "\n    ".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"  <head><title>{title}</title></head>\n"
        f"  <body>\n    <h1>{title}</h1>\n    {body}\n  </body>\n</html>"
    )


@pytest.fixture
def multi_file_epub(tmp_path: Path) -> Path:
    """The ordinary layout: one XHTML per chapter, an EPUB3 navigation document, nested entries."""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Timeless Way of Building</dc:title>
    <dc:creator>Christopher Alexander</dc:creator>
    <dc:date>1979</dc:date>
    <dc:publisher>Oxford University Press</dc:publisher>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="text/ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>"""
    nav = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head><title>Contents</title></head>
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="text/ch1.xhtml">The Quality</a>
          <ol><li><a href="text/ch1.xhtml#named">It cannot be named</a></li></ol>
        </li>
        <li><a href="text/ch2.xhtml">The Gate</a></li>
      </ol>
    </nav>
  </body>
</html>"""
    chapter_one = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        "  <head><title>The Quality</title></head>\n"
        "  <body>\n"
        "    <h1>The Quality</h1>\n"
        "    <p>There is a central quality which is the root criterion of life.</p>\n"
        '    <h2 id="named">It cannot be named</h2>\n'
        "    <p>The&nbsp;word we are looking for does not exist in any language.</p>\n"
        "  </body>\n</html>"
    )
    return write_epub(
        tmp_path / "timeless-way.epub",
        {
            "OEBPS/content.opf": opf,
            "OEBPS/nav.xhtml": nav,
            "OEBPS/text/ch1.xhtml": chapter_one,
            "OEBPS/text/ch2.xhtml": _chapter_xhtml(
                "The Gate", ["To reach the quality we must first build a living language."]
            ),
        },
    )


@pytest.fixture
def single_file_epub(tmp_path: Path) -> Path:
    """The trap layout: one XHTML holding every chapter, an EPUB2 NCX pointing at anchors."""
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Thinking in Systems</dc:title>
    <dc:creator>Donella Meadows</dc:creator>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="book" href="text/book.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="book"/>
  </spine>
</package>"""
    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1" playOrder="1">
      <navLabel><text>The Basics</text></navLabel>
      <content src="text/book.xhtml#basics"/>
      <navPoint id="n1a" playOrder="2">
        <navLabel><text>Stocks and flows</text></navLabel>
        <content src="text/book.xhtml#stocks"/>
      </navPoint>
    </navPoint>
    <navPoint id="n2" playOrder="3">
      <navLabel><text>Leverage Points</text></navLabel>
      <content src="text/book.xhtml#leverage"/>
    </navPoint>
  </navMap>
</ncx>"""
    book = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        "  <head><title>Thinking in Systems</title></head>\n"
        "  <body>\n"
        "    <p>A short preface that belongs to no chapter at all.</p>\n"
        '    <section id="basics">\n'
        "      <h1>The Basics</h1>\n"
        "      <p>A system is more than the sum of its parts.</p>\n"
        '      <h2 id="stocks">Stocks and flows</h2>\n'
        "      <p>A stock is the memory of the history of changing flows.</p>\n"
        "    </section>\n"
        '    <section id="leverage">\n'
        "      <h1>Leverage Points</h1>\n"
        "      <p>Places in a system where a small shift produces big changes.</p>\n"
        "    </section>\n"
        "  </body>\n</html>"
    )
    return write_epub(
        tmp_path / "thinking-in-systems.epub",
        {"OEBPS/content.opf": opf, "OEBPS/toc.ncx": ncx, "OEBPS/text/book.xhtml": book},
    )


# --------------------------------------------------------------------------------------
# HTML fixture
# --------------------------------------------------------------------------------------

ARTICLE_HTML = """<!DOCTYPE html>
<html><head>
<title>What a Reference Actually Is</title>
<meta property="article:published_time" content="2024-03-05"/>
<meta property="og:site_name" content="Example Journal"/>
<meta name="author" content="Jane Roe"/>
</head><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<article>
<h1>What a Reference Actually Is</h1>
<p>A reference is source-derived knowledge, and it stays source-derived however useful it becomes
to the person reading it, which is the distinction the whole tree rests upon.</p>
<h2>Why the distinction matters</h2>
<p>Blur it and the knowledge base slowly fills with machine-written experience, and the rule that
human content wins stops meaning anything at all because there is no human side left to win.</p>
<h2>What changes when a human adopts one</h2>
<p>The human writes a note in their own words, and from that moment it is theirs, carrying their
judgement about their own circumstances rather than the author's about theirs.</p>
</article>
<footer>Copyright notice and a pile of unrelated links.</footer>
</body></html>"""


# --------------------------------------------------------------------------------------
# PDF — structure recovery
# --------------------------------------------------------------------------------------


def test_pdf_outline_gives_the_documents_own_structure(outlined_pdf: Path) -> None:
    """LS-10: the outline is the source's own structure, so it is the key re-ingestion matches on."""
    source = extract(outlined_pdf)

    assert source.kind == "pdf"
    assert source.title == "Radical Candor"
    assert source.page_count == 3
    assert source.structure_method is StructureMethod.PDF_OUTLINE
    assert source.structure_method.is_intrinsic
    assert [(s.title, s.level) for s in source.sections] == [
        ("(front matter)", 1),
        ("Challenge directly", 1),
        ("Say it once", 2),
        ("Care personally", 1),
    ]


def test_pdf_outline_sections_carry_their_own_text(outlined_pdf: Path) -> None:
    source = extract(outlined_pdf)
    by_title = {section.title: section.text for section in source.sections}

    assert "preface" in by_title["(front matter)"]
    assert by_title["Challenge directly"] == "Say the thing that is true and hard."
    assert by_title["Say it once"] == "Repeating it is punishment, not clarity."
    assert by_title["Care personally"] == "Mean it, or the first half is cruelty."
    # The heading opens its own slice; it is the title, not the first line of the body.
    assert not by_title["Care personally"].startswith("Care personally")


def test_an_outline_entry_missing_from_the_text_never_steals_a_chapters_body(
    tmp_path: Path,
) -> None:
    """The chapter keeps its text and the unlocatable entry is reported, not silently preferred.

    Some outlines name a heading the page renders as an image, or word it differently from the
    page. Both entries then resolve to the same offset, and slicing between equal offsets hands the
    whole chapter to the second one — losing the body of the very chapter LS-10 reconciles on.
    """
    raw = make_pdf([[("Real chapter", 20.0, 700.0), ("The body of the chapter.", 11.0, 670.0)]])
    path = tmp_path / "mismatched.pdf"
    path.write_bytes(
        add_outline(raw, [("Real chapter", 1, 0), ("A heading drawn as a picture", 2, 0)])
    )

    source = extract(path)

    assert [s.title for s in source.sections] == ["Real chapter"]
    assert source.sections[0].text == "The body of the chapter."
    assert any("A heading drawn as a picture" in warning for warning in source.warnings)


def test_pdf_outline_splits_two_chapters_sharing_one_page(tmp_path: Path) -> None:
    """A page number alone cannot separate two chapters; locating the title in the page can."""
    raw = make_pdf(
        [
            [
                ("Short chapter", 18.0, 700.0),
                ("It says one thing and stops.", 11.0, 670.0),
                ("Next chapter", 18.0, 620.0),
                ("It says a different thing.", 11.0, 590.0),
            ]
        ]
    )
    path = tmp_path / "crowded.pdf"
    path.write_bytes(add_outline(raw, [("Short chapter", 1, 0), ("Next chapter", 1, 0)]))

    sections = extract(path).sections

    assert [s.title for s in sections] == ["Short chapter", "Next chapter"]
    assert sections[0].text == "It says one thing and stops."
    assert sections[1].text == "It says a different thing."


def test_every_chapter_of_a_long_source_is_present_and_distinct(tmp_path: Path) -> None:
    """The property the whole feature exists for: nothing gets dropped and nothing gets merged.

    A single-turn ingestion reads what fits and reports success, with nothing recording that the
    later chapters were never opened. Extraction is what makes that checkable — twenty chapters in
    means twenty sections out, each holding its own text and only its own.
    """
    chapters = 20
    pages = [
        [(f"Chapter {n}", 20.0, 700.0), (f"The distinctive claim of chapter {n}.", 11.0, 660.0)]
        for n in range(1, chapters + 1)
    ]
    raw = make_pdf(pages)
    path = tmp_path / "long.pdf"
    path.write_bytes(add_outline(raw, [(f"Chapter {n}", 1, n - 1) for n in range(1, chapters + 1)]))

    sections = extract(path).sections

    assert [s.title for s in sections] == [f"Chapter {n}" for n in range(1, chapters + 1)]
    for index, section in enumerate(sections, start=1):
        assert section.text == f"The distinctive claim of chapter {index}."


def test_pdf_without_outline_falls_back_to_font_size(tmp_path: Path) -> None:
    """No outline: the effective size ``abs(font_size * tm[3])`` recovers the heading levels."""
    path = tmp_path / "no-outline.pdf"
    path.write_bytes(
        make_pdf(
            [
                [
                    ("Method", 20.0, 700.0),
                    ("We measured the thing carefully and wrote down what we saw.", 11.0, 670.0),
                    ("Instrumentation", 14.0, 620.0),
                    ("A description of the apparatus used throughout the study.", 11.0, 590.0),
                ],
                [
                    ("Results", 20.0, 700.0),
                    ("The numbers came out broadly as the hypothesis predicted.", 11.0, 670.0),
                ],
            ]
        )
    )

    source = extract(path)

    assert source.structure_method is StructureMethod.PDF_FONT_SIZE
    assert not source.structure_method.is_intrinsic, "an inferred heading is not a stable key"
    assert [(s.title, s.level) for s in source.sections] == [
        ("Method", 1),
        ("Instrumentation", 2),
        ("Results", 1),
    ]
    assert "apparatus" in source.sections[1].text


def test_pdf_with_uniform_type_is_one_whole_document(tmp_path: Path) -> None:
    """No outline and no size contrast is not a failure — it is a source with no structure."""
    path = tmp_path / "flat.pdf"
    path.write_bytes(
        make_pdf([[("One paragraph set in one size, saying one thing.", 11.0, 700.0)]])
    )

    source = extract(path)

    assert source.structure_method is StructureMethod.WHOLE_DOCUMENT
    assert len(source.sections) == 1
    assert source.sections[0].title == "flat"


def test_pdf_font_size_reads_the_text_matrix_not_the_operand(tmp_path: Path) -> None:
    """Every run is 11pt; only the body has a body-sized *drawn* size, so nothing is a heading."""
    path = tmp_path / "same-size.pdf"
    path.write_bytes(
        make_pdf(
            [
                [
                    ("First line of a wholly unstructured document.", 11.0, 700.0),
                    ("Second line, the same size as the first one.", 11.0, 680.0),
                ]
            ]
        )
    )

    assert extract(path).structure_method is StructureMethod.WHOLE_DOCUMENT


# --------------------------------------------------------------------------------------
# PDF — the hostile inputs, each with its own error
# --------------------------------------------------------------------------------------


def test_scanned_pdf_raises_rather_than_returning_silence(tmp_path: Path) -> None:
    """The failure this module exists to prevent: images, no text, and no library raises."""
    path = tmp_path / "scanned.pdf"
    path.write_bytes(make_pdf([[], []], with_image=True))

    with pytest.raises(ScannedSourceError, match="OCR"):
        extract(path)


def test_blank_pdf_is_reported_as_blank_not_as_scanned(tmp_path: Path) -> None:
    """Zero text with no images is a different problem, and OCR would not fix it."""
    path = tmp_path / "blank.pdf"
    path.write_bytes(make_pdf([[], []]))

    with pytest.raises(EmptySourceError) as caught:
        extract(path)
    assert not isinstance(caught.value, ScannedSourceError)


def test_encrypted_pdf_raises_its_own_error(tmp_path: Path) -> None:
    writer = PdfWriter(clone_from=io.BytesIO(make_pdf(BOOK_PAGES)))
    writer.encrypt("a-password-the-caller-does-not-have")
    path = tmp_path / "locked.pdf"
    buffer = io.BytesIO()
    writer.write(buffer)
    path.write_bytes(buffer.getvalue())

    with pytest.raises(EncryptedSourceError):
        extract(path)


def test_garbled_pdf_is_not_mistaken_for_a_blank_one(tmp_path: Path) -> None:
    """A font with no character map yields private-use glyphs — text to a checker, noise to a human.

    The ordering inside the guard is what this test pins: private-use characters are not
    alphanumeric, so an emptiness-first check would call this document scanned and send the caller
    to OCR a document that has perfectly good text and a broken font.
    """
    path = tmp_path / "garbled.pdf"
    path.write_bytes(make_pdf([[("ABCDEFGHIJKLMNOPQRSTUVWXYZ", 12.0, 700.0)]], garbled=True))

    with pytest.raises(GarbledTextError, match="unmappable"):
        extract(path)


def test_pdf_that_is_not_a_pdf_is_malformed_not_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "liar.pdf"
    path.write_bytes(b"this was never a PDF, whatever the name says")

    with pytest.raises(MalformedSourceError, match="PDF header"):
        extract(path)


def test_pdf_records_pages_that_yielded_nothing(tmp_path: Path) -> None:
    """Partial extraction is reported rather than smoothed over — half a book is not a whole one."""
    path = tmp_path / "half-scanned.pdf"
    path.write_bytes(
        make_pdf(
            [[("Chapter One", 20.0, 700.0), ("Real text on the first page.", 11.0, 670.0)], [], []],
            with_image=True,
        )
    )

    source = extract(path)

    assert source.warnings == ("2 of 3 pages yielded no text",)


# --------------------------------------------------------------------------------------
# EPUB
# --------------------------------------------------------------------------------------


def test_epub3_nav_recovers_nested_chapters(multi_file_epub: Path) -> None:
    source = extract(multi_file_epub)

    assert source.kind == "epub"
    assert source.title == "The Timeless Way of Building"
    assert source.author == "Christopher Alexander"
    assert source.published == "1979"
    assert source.site == "Oxford University Press"
    assert source.structure_method is StructureMethod.EPUB_NAV
    assert [(s.title, s.level) for s in source.sections] == [
        ("The Quality", 1),
        ("It cannot be named", 2),
        ("The Gate", 1),
    ]


def test_epub3_sections_carry_the_right_text(multi_file_epub: Path) -> None:
    by_title = {section.title: section.text for section in extract(multi_file_epub).sections}

    assert "root criterion of life" in by_title["The Quality"]
    assert "root criterion" not in by_title["It cannot be named"]
    assert "living language" in by_title["The Gate"]


def test_epub_expands_html_entities_xml_does_not_define(multi_file_epub: Path) -> None:
    """``&nbsp;`` is undefined in XML and makes ElementTree refuse the whole chapter."""
    by_title = {section.title: section.text for section in extract(multi_file_epub).sections}

    assert "The word we are looking for" in by_title["It cannot be named"].replace("\xa0", " ")


def test_single_file_epub_slices_between_anchors(single_file_epub: Path) -> None:
    """The layout that breaks naive readers: every chapter is an anchor into one XHTML file."""
    source = extract(single_file_epub)

    assert source.structure_method is StructureMethod.EPUB_NCX
    assert [(s.title, s.level) for s in source.sections] == [
        ("(front matter)", 1),
        ("The Basics", 1),
        ("Stocks and flows", 2),
        ("Leverage Points", 1),
    ]
    by_title = {section.title: section.text for section in source.sections}
    assert by_title["(front matter)"] == "A short preface that belongs to no chapter at all."
    assert "more than the sum of its parts" in by_title["The Basics"]
    assert "memory of the history" in by_title["Stocks and flows"]
    assert "memory of the history" not in by_title["The Basics"]
    assert "small shift produces big changes" in by_title["Leverage Points"]


def test_epub_without_a_table_of_contents_falls_back_to_the_spine(tmp_path: Path) -> None:
    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>No Contents</dc:title></metadata>
  <manifest>
    <item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>
    <item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="a"/><itemref idref="b"/></spine>
</package>"""
    path = write_epub(
        tmp_path / "no-toc.epub",
        {
            "OEBPS/content.opf": opf,
            "OEBPS/a.xhtml": _chapter_xhtml("Opening", ["The first thing that is said."]),
            "OEBPS/b.xhtml": _chapter_xhtml("Closing", ["The last thing that is said."]),
        },
    )

    source = extract(path)

    assert source.structure_method is StructureMethod.EPUB_SPINE
    assert [s.title for s in source.sections] == ["Opening", "Closing"]


def test_epub_resolves_hrefs_against_the_opf_directory(single_file_epub: Path) -> None:
    """The NCX says ``text/book.xhtml``; the file is at ``OEBPS/text/book.xhtml``.

    Resolving against the zip root instead makes every chapter href miss, and the extraction then
    quietly degrades to one unsplit section rather than failing.
    """
    assert extract(single_file_epub).structure_method is StructureMethod.EPUB_NCX


def test_epub_missing_its_package_document_is_malformed(tmp_path: Path) -> None:
    path = tmp_path / "broken.epub"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER_XML)

    with pytest.raises(MalformedSourceError):
        extract(path)


# --------------------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------------------


def test_html_keeps_its_headings_and_records_its_metadata(tmp_path: Path) -> None:
    """trafilatura's default plain text returns zero headings; markdown output is what LS-10 needs."""
    path = tmp_path / "reference.html"
    path.write_text(ARTICLE_HTML, encoding="utf-8")

    source = extract(path)

    assert source.kind == "html"
    assert source.title == "What a Reference Actually Is"
    assert source.published == "2024-03-05"
    assert source.site == "Example Journal"
    assert source.structure_method is StructureMethod.HTML_HEADINGS
    assert [(s.title, s.level) for s in source.sections] == [
        ("What a Reference Actually Is", 1),
        ("Why the distinction matters", 2),
        ("What changes when a human adopts one", 2),
    ]


def test_html_drops_the_navigation_and_the_footer(tmp_path: Path) -> None:
    path = tmp_path / "reference.html"
    path.write_text(ARTICLE_HTML, encoding="utf-8")

    body = "\n".join(section.text for section in extract(path).sections)

    assert "unrelated links" not in body
    assert "human content wins" in body


def test_html_with_no_article_text_raises(tmp_path: Path) -> None:
    """A page with a title and no prose is a failed capture, not a source with a short summary."""
    path = tmp_path / "chrome-only.html"
    path.write_text(
        "<html><head><title>Nothing</title><style>body{color:red}</style></head>"
        "<body><script>var x = 1;</script></body></html>",
        encoding="utf-8",
    )

    with pytest.raises(EmptySourceError):
        extract(path)


# --------------------------------------------------------------------------------------
# Text and markdown
# --------------------------------------------------------------------------------------


def test_markdown_headings_become_sections(tmp_path: Path) -> None:
    path = tmp_path / "notes-on-feedback.md"
    path.write_text(
        "# On Feedback\n\nAn opening claim.\n\n"
        "## Say it early\n\nSaying it late is a different message.\n\n"
        "### And say it once\n\nRepetition is punishment, not clarity.\n",
        encoding="utf-8",
    )

    source = extract(path)

    assert source.kind == "text"
    assert source.title == "On Feedback"
    assert source.structure_method is StructureMethod.MARKDOWN_HEADINGS
    assert [(s.title, s.level) for s in source.sections] == [
        ("On Feedback", 1),
        ("Say it early", 2),
        ("And say it once", 3),
    ]
    assert source.sections[2].text == "Repetition is punishment, not clarity."


def test_a_hash_inside_a_fenced_block_is_not_a_chapter(tmp_path: Path) -> None:
    path = tmp_path / "with-code.md"
    path.write_text(
        "# Real heading\n\n```bash\n# not a heading, a shell comment\nls -la\n```\n\nAfter.\n",
        encoding="utf-8",
    )

    assert [s.title for s in extract(path).sections] == ["Real heading"]


def test_shallowest_heading_becomes_level_one(tmp_path: Path) -> None:
    """A page whose top heading is ``##`` still has chapters, and they are level 1 chapters."""
    path = tmp_path / "shifted.md"
    path.write_text("## First\n\nText.\n\n### Nested\n\nMore text.\n", encoding="utf-8")

    assert [(s.title, s.level) for s in extract(path).sections] == [("First", 1), ("Nested", 2)]


def test_plain_text_without_headings_is_one_section(tmp_path: Path) -> None:
    path = tmp_path / "clip.txt"
    path.write_text("A single claim, and the evidence offered for it.\n", encoding="utf-8")

    source = extract(path)

    assert source.structure_method is StructureMethod.WHOLE_DOCUMENT
    assert len(source.sections) == 1
    assert source.sections[0].title == "clip"


def test_garbled_text_file_raises(tmp_path: Path) -> None:
    """The guard is on the text, not on the container, so it covers every kind of source."""
    path = tmp_path / "mojibake.txt"
    path.write_text("real words " + "".join(chr(0xE000 + n) for n in range(60)), encoding="utf-8")

    with pytest.raises(GarbledTextError):
        extract(path)


def test_a_few_private_use_characters_do_not_trip_the_guard(tmp_path: Path) -> None:
    """The threshold is a proportion: a source that happens to carry icon glyphs is still good."""
    path = tmp_path / "with-icons.md"
    path.write_text(
        "# Setup\n\nPress the \ue0a0 key, then read the rest of this perfectly ordinary "
        "paragraph about setting the thing up correctly the first time.\n",
        encoding="utf-8",
    )

    assert extract(path).sections[0].title == "Setup"


def test_empty_text_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "nothing.txt"
    path.write_text("   \n\n  \n", encoding="utf-8")

    with pytest.raises(EmptySourceError):
        extract(path)


# --------------------------------------------------------------------------------------
# detect_kind
# --------------------------------------------------------------------------------------


def test_magic_bytes_beat_the_file_extension(tmp_path: Path) -> None:
    """A download named ``.txt`` that is really a PDF should be read as a PDF."""
    path = tmp_path / "download.txt"
    path.write_bytes(make_pdf([[("Something readable on a page.", 11.0, 700.0)]]))

    assert detect_kind(path) == "pdf"
    assert extract(path).kind == "pdf"


def test_an_epub_is_recognised_without_its_extension(tmp_path: Path) -> None:
    source = write_epub(tmp_path / "unnamed", {"OEBPS/content.opf": "<package/>"})

    assert detect_kind(source) == "epub"


def test_unknown_binary_is_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "mystery.bin"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")

    with pytest.raises(UnsupportedSourceError):
        detect_kind(path)


def test_a_missing_source_says_so(tmp_path: Path) -> None:
    with pytest.raises(SourceNotFoundError):
        extract(tmp_path / "never-existed.pdf")


# --------------------------------------------------------------------------------------
# Staging (LS-8, LS-9)
# --------------------------------------------------------------------------------------


@pytest.fixture
def kb_root(tmp_path: Path) -> Path:
    root = tmp_path / "kb"
    root.mkdir()
    scaffold_topic(
        root,
        "Management",
        title="Management",
        description="Leading people and running teams",
        today=date(2026, 8, 7),
    )
    return root


def test_stage_keeps_both_the_original_and_the_extraction(
    kb_root: Path, outlined_pdf: Path
) -> None:
    """LS-7: the extraction is what the loop reads, the original is what a topic gets a copy of."""
    staged = stage(kb_root, str(outlined_pdf))

    assert staged.root == kb_root / INBOX_DIR / "radical-candor"
    assert staged.original.read_bytes() == outlined_pdf.read_bytes()
    assert staged.original.name == "radical-candor.pdf"
    assert staged.extraction_path.is_file()
    assert staged.text_path.is_file()
    assert staged.from_cache is False
    assert staged.extracted.structure_method is StructureMethod.PDF_OUTLINE


def test_the_staging_directory_holds_exactly_four_files(kb_root: Path, outlined_pdf: Path) -> None:
    """The on-disk contract the ingestion loop reads, pinned so it cannot drift silently."""
    staged = stage(kb_root, str(outlined_pdf))

    assert sorted(entry.name for entry in staged.root.iterdir()) == [
        "radical-candor.extracted.json",
        "radical-candor.extracted.md",
        "radical-candor.pdf",
        "source.json",
    ]


def test_a_markdown_source_survives_its_own_extraction(kb_root: Path) -> None:
    """Staging a `.md` file must not overwrite it with the rendered extraction (LS-7, LS-8).

    The original and the rendered markdown are both markdown, so the earlier layout — original at
    `<slug>.md`, rendering at `<slug>.md` — wrote one over the other and the manifest went on
    calling the survivor the original. Every LS-1 copy into a topic then carried the extraction
    while claiming to carry the source, silently, with the whole suite green: nothing else staged a
    markdown file, and for a PDF the two names differ so the collision could not happen.

    What makes this test worth its length is that the failure is invisible in a diff of a
    well-formed document. Blank lines move; nothing looks wrong. So it asserts on the bytes.
    """
    original = kb_root.parent / "notes.md"
    body = (
        "# Notes\n## First\nNo blank line after the heading.\n\n\n## Second\nTrailing spaces.  \n"
    )
    original.write_text(body, encoding="utf-8")

    staged = stage(kb_root, str(original))

    assert staged.original.read_bytes() == body.encode("utf-8"), "the original must be untouched"
    assert staged.original != staged.text_path
    assert staged.text_path.read_text(encoding="utf-8") == render_markdown(
        staged.extracted.sections
    )
    assert staged.text_path.read_text(encoding="utf-8") != body, (
        "the rendering genuinely differs from the source here — otherwise this test proves nothing"
    )


def test_a_staging_directory_from_another_schema_is_not_a_cache_hit(
    kb_root: Path, outlined_pdf: Path
) -> None:
    """A manifest whose layout we no longer understand is re-staged, not trusted (LS-9).

    Version 1's `original` may be the extraction rather than the source, so honouring its manifest
    would carry that bug forward for every already-staged source.
    """
    staged = stage(kb_root, str(outlined_pdf))
    manifest = staged.root / "source.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["schema"] = 1
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert find_staged(kb_root, str(outlined_pdf)) is None
    assert stage(kb_root, str(outlined_pdf)).from_cache is False


def test_an_origin_may_be_given_as_a_path(kb_root: Path, outlined_pdf: Path) -> None:
    staged = stage(kb_root, outlined_pdf)

    assert staged.origin == str(outlined_pdf)
    assert find_staged(kb_root, outlined_pdf) is not None


def test_staged_markdown_is_the_extraction_rendered(kb_root: Path, outlined_pdf: Path) -> None:
    staged = stage(kb_root, str(outlined_pdf))

    assert staged.text_path.read_text(encoding="utf-8") == render_markdown(
        staged.extracted.sections
    )
    assert "# Challenge directly" in staged.text_path.read_text(encoding="utf-8")
    assert "## Say it once" in staged.text_path.read_text(encoding="utf-8")


def test_staging_the_same_source_twice_is_a_cache_hit(kb_root: Path, outlined_pdf: Path) -> None:
    """LS-9: re-ingestion re-reads the cache rather than paying for extraction again."""
    first = stage(kb_root, str(outlined_pdf))
    marker = json.loads(first.extraction_path.read_text(encoding="utf-8"))
    marker["title"] = "Edited In Place"
    first.extraction_path.write_text(json.dumps(marker), encoding="utf-8")

    second = stage(kb_root, str(outlined_pdf))

    assert second.from_cache is True
    assert second.extracted.title == "Edited In Place", "the cache was re-read, not rebuilt"


def test_refresh_forces_the_extraction_again(kb_root: Path, outlined_pdf: Path) -> None:
    first = stage(kb_root, str(outlined_pdf))
    marker = json.loads(first.extraction_path.read_text(encoding="utf-8"))
    marker["title"] = "Edited In Place"
    first.extraction_path.write_text(json.dumps(marker), encoding="utf-8")

    refreshed = stage(kb_root, str(outlined_pdf), refresh=True)

    assert refreshed.from_cache is False
    assert refreshed.extracted.title == "Radical Candor"


def test_find_staged_answers_the_ls11_question(kb_root: Path, outlined_pdf: Path) -> None:
    """LS-11: the agent says "this is already here" and asks, rather than silently re-reading."""
    assert find_staged(kb_root, str(outlined_pdf)) is None

    stage(kb_root, str(outlined_pdf))

    found = find_staged(kb_root, str(outlined_pdf))
    assert found is not None
    assert found.from_cache is True
    assert found.extracted.title == "Radical Candor"


def test_a_url_is_fetched_and_the_url_is_the_recorded_origin(kb_root: Path) -> None:
    """The origin outlives staging: it is what the source file's provenance block records."""
    calls: list[str] = []

    def fake_fetch(url: str) -> Fetched:
        calls.append(url)
        return Fetched(body=ARTICLE_HTML.encode("utf-8"), content_type="text/html; charset=utf-8")

    staged = stage(kb_root, "https://example.org/posts/what-a-reference-is", fetch=fake_fetch)

    assert calls == ["https://example.org/posts/what-a-reference-is"]
    assert staged.slug == "what-a-reference-is"
    assert staged.original.name == "what-a-reference-is.html"
    assert staged.extracted.origin == "https://example.org/posts/what-a-reference-is"
    assert staged.extracted.site == "Example Journal"
    assert staged.extracted.sections[0].title == "What a Reference Actually Is"


def test_two_sources_with_the_same_name_get_separate_directories(
    kb_root: Path, tmp_path: Path
) -> None:
    first_dir = tmp_path / "one"
    second_dir = tmp_path / "two"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "paper.md").write_text("# Alpha\n\nThe first paper.\n", encoding="utf-8")
    (second_dir / "paper.md").write_text("# Beta\n\nThe second paper.\n", encoding="utf-8")

    first = stage(kb_root, str(first_dir / "paper.md"))
    second = stage(kb_root, str(second_dir / "paper.md"))

    assert first.slug == "paper"
    assert second.slug == "paper-2"
    assert first.extracted.title == "Alpha"
    assert second.extracted.title == "Beta"


def test_staging_into_a_missing_knowledge_base_says_so(tmp_path: Path, outlined_pdf: Path) -> None:
    with pytest.raises(SourceNotFoundError):
        stage(tmp_path / "no-such-kb", str(outlined_pdf))


def test_staging_a_missing_file_says_so(kb_root: Path, tmp_path: Path) -> None:
    with pytest.raises(SourceNotFoundError):
        stage(kb_root, str(tmp_path / "absent.pdf"))


def test_a_scanned_source_fails_at_staging_before_any_topic_sees_it(
    kb_root: Path, tmp_path: Path
) -> None:
    """LS-7: extraction quality is visible rather than assumed, and it fails at the start."""
    scanned = tmp_path / "scanned.pdf"
    scanned.write_bytes(make_pdf([[], []], with_image=True))

    with pytest.raises(ScannedSourceError):
        stage(kb_root, str(scanned))


# --------------------------------------------------------------------------------------
# LS-8 — the inbox is inside the tree and invisible to it
# --------------------------------------------------------------------------------------


def test_the_inbox_is_invisible_to_every_layer_one_walk(kb_root: Path, outlined_pdf: Path) -> None:
    """LS-8's whole claim, asserted rather than trusted.

    ``.inbox`` is dot-prefixed, so Layer 1's PA-16 walk skips it: nothing staged is scanned,
    nothing staged is validated, and nothing staged reaches the tag registry. This is what lets a
    source live inside the tree before any topic has earned a copy of it, with no change to Layer 1
    and no second mount.
    """
    before = scan(kb_root)
    staged = stage(kb_root, str(outlined_pdf))
    after = scan(kb_root)

    assert staged.root.is_dir(), "the source really is inside the knowledge base"
    assert after.files.keys() == before.files.keys()
    assert after.topics.keys() == before.topics.keys()
    assert not any(INBOX_DIR in path for path in after.files)
    assert not any(
        INBOX_DIR in str(finding.path or "") for finding in validate_tree(kb_root, after)
    )


def test_inbox_root_is_dot_prefixed(kb_root: Path) -> None:
    assert inbox_root(kb_root).name.startswith(".")
    assert inbox_root(kb_root) == kb_root / ".inbox"


# --------------------------------------------------------------------------------------
# The shapes themselves
# --------------------------------------------------------------------------------------


def test_extraction_survives_a_json_round_trip(tmp_path: Path, outlined_pdf: Path) -> None:
    source = extract(outlined_pdf)
    path = tmp_path / "cache.json"

    save_extraction(source, path)

    assert load_extraction(path) == source


def test_a_corrupt_cache_is_reported_not_silently_ignored(tmp_path: Path) -> None:
    path = tmp_path / "cache.json"
    path.write_text("{not json at all", encoding="utf-8")

    with pytest.raises(MalformedSourceError):
        load_extraction(path)


def test_render_markdown_is_the_ls10_shape() -> None:
    """LS-10: one heading per chapter, the arguments underneath, in the source's own order."""
    rendered = render_markdown(
        [
            Section(title="Chapter 2 — Challenge directly", level=1, text="- an argument"),
            Section(title="Detail", level=2, text="- a nested argument"),
            Section(title="Across the source", level=1, text=""),
        ]
    )

    assert rendered == (
        "# Chapter 2 — Challenge directly\n\n- an argument\n\n"
        "## Detail\n\n- a nested argument\n\n"
        "# Across the source\n"
    )


def test_section_reports_its_own_size_and_emptiness() -> None:
    filled = Section(title="A", level=1, text="four")
    empty = Section(title="B", level=2, text="  \n ")

    assert filled.char_count == 4
    assert not filled.is_empty
    assert empty.is_empty


def test_with_origin_replaces_only_the_origin(outlined_pdf: Path) -> None:
    source = extract(outlined_pdf)

    moved = source.with_origin("https://example.org/book")

    assert moved.origin == "https://example.org/book"
    assert moved.sections == source.sections
    assert moved.title == source.title


def test_char_count_sums_the_sections(outlined_pdf: Path) -> None:
    source = extract(outlined_pdf)

    assert source.char_count == sum(len(s.text) for s in source.sections)
    assert source.text == render_markdown(source.sections)


def test_extracted_source_is_immutable(outlined_pdf: Path) -> None:
    """A staged extraction is read by a loop that must not be able to rewrite history mid-pass."""
    source = extract(outlined_pdf)

    with pytest.raises(AttributeError):
        source.title = "something else"  # type: ignore[misc]


def test_structure_method_names_intrinsic_structure_apart_from_inferred() -> None:
    intrinsic = {
        StructureMethod.PDF_OUTLINE,
        StructureMethod.EPUB_NAV,
        StructureMethod.EPUB_NCX,
        StructureMethod.EPUB_SPINE,
        StructureMethod.HTML_HEADINGS,
        StructureMethod.MARKDOWN_HEADINGS,
    }

    for method in StructureMethod:
        assert method.is_intrinsic is (method in intrinsic), method


def test_extracted_source_defaults_are_conservative() -> None:
    source = ExtractedSource(
        title="t",
        kind="text",
        origin="o",
        sections=(),
        structure_method=StructureMethod.WHOLE_DOCUMENT,
    )

    assert source.author is None
    assert source.published is None
    assert source.site is None
    assert source.page_count is None
    assert source.warnings == ()


# --------------------------------------------------------------------------------------
# Audit regressions, 2026-08-07 — each one reproduced against the shipped code first
# --------------------------------------------------------------------------------------


def test_a_source_named_like_the_manifest_is_not_overwritten_by_it_ls8(kb_root: Path) -> None:
    """`source.json` is the manifest's fixed name and a source can slug to exactly it (LS-8).

    The derived files were made defensive by construction and the manifest was not, so a file called
    `source.json` — or `!!!.json`, or anything whose stem slugifies to nothing — had its preserved
    original overwritten by the manifest written moments later, and `_load_staged` then served the
    manifest as the original forever, satisfied that a file by that name existed.
    """
    original = kb_root.parent / "source.json"
    original.write_bytes(b'{"my": "hand-written notes", "value": 42}')

    staged = stage(kb_root, original)

    assert staged.original.read_bytes() == original.read_bytes()
    assert staged.original.name == "source.original.json"
    assert stage(kb_root, original).original.read_bytes() == original.read_bytes()


def test_one_document_spelled_three_ways_is_one_source_ls5(
    kb_root: Path, outlined_pdf: Path
) -> None:
    """`~/b.pdf`, `./b.pdf` and `/x/../x/b.pdf` are the same book, so they stage once (LS-5, LS-9).

    Uncanonicalised, each spelling was its own source: the book was read once per spelling, landed
    in its own reference folder with its own copy of a 19 MB PDF, and reconciliation never ran
    because no pass had anything to reconcile against — the "near-identical parts under slightly
    different names" that the one-file-per-source layout exists to prevent, at whole-source scale.
    """
    first = stage(kb_root, outlined_pdf)
    dotted = stage(kb_root, outlined_pdf.parent / "." / outlined_pdf.name)
    doubled = stage(kb_root, outlined_pdf.parent / "sub" / ".." / outlined_pdf.name)

    assert first.slug == dotted.slug == doubled.slug
    assert dotted.from_cache and doubled.from_cache
    assert len(list((kb_root / INBOX_DIR).iterdir())) == 1


def test_a_changed_source_is_restaged_rather_than_served_from_cache_ls5(kb_root: Path) -> None:
    """The commonest reason to re-ingest a local file is that it changed (LS-5, LS-9).

    Keyed on the origin string alone, the cache handed back the superseded text with a reading dated
    today: new chapters invisible, corrections invisible, and LS-1's copy in the topic holding the
    first edition while the provenance points at a path that now holds something else.
    """
    source = kb_root.parent / "edition.txt"
    source.write_text("## Fire\n\nUse lighter fluid, it is fine.\n", encoding="utf-8")
    stage(kb_root, source)

    source.write_text(
        "## Fire\n\nNever use lighter fluid.\n\n## Smoke\n\nDry wood.\n", encoding="utf-8"
    )
    again = stage(kb_root, source)

    assert again.from_cache is False
    assert [section.title for section in again.extracted.sections] == ["Fire", "Smoke"]
    assert again.original.read_bytes() == source.read_bytes()


def test_a_failed_restage_leaves_the_previous_staging_whole_ls7(kb_root: Path) -> None:
    """`refresh=True` writes the new bytes only if the new extraction succeeds (LS-7, LS-9).

    In place, a failed re-extraction left edition two's original beside edition one's extraction,
    with a manifest asserting they were the same document — served as a clean cache hit forever
    after. The loop would then file arguments from one edition while LS-1 copied the other into
    every topic, as the file a human opens to check a claim.
    """
    source = kb_root.parent / "book.txt"
    source.write_text("## Chapter 1\n\nEdition one says searing works.\n", encoding="utf-8")
    stage(kb_root, source)
    source.write_text("— — —\n", encoding="utf-8")  # no alphanumerics: EmptySourceError

    with pytest.raises(EmptySourceError):
        stage(kb_root, source, refresh=True)

    held = find_staged(kb_root, source)
    assert held is not None
    assert held.extracted.sections[0].text.startswith("Edition one")
    assert held.original.read_text(encoding="utf-8").startswith("## Chapter 1")


def test_a_utf16_text_source_is_decoded_not_mojibaked_ls7(kb_root: Path) -> None:
    """cp1252 "succeeds" on UTF-16, so the guard has to look at the byte-order mark (LS-7).

    Notepad's "Unicode" and PowerShell 5.1's redirection both write this. Decoded as cp1252 it is
    not empty, so `_guard_text` passed it, and the expert was asked to find arguments in
    `ÿþ#\\x00 \\x00D\\x00…` — the loud failure LS-7 promises, failing in the worse direction.
    """
    source = kb_root.parent / "notes.txt"
    source.write_bytes("# Deliberate Practice\n\nFeedback is the mechanism.\n".encode("utf-16"))

    staged = stage(kb_root, source)

    assert staged.extracted.title == "Deliberate Practice"
    assert "\x00" not in staged.text_path.read_text(encoding="utf-8")
    assert staged.original.read_bytes() == source.read_bytes()


def test_text_that_is_really_binary_is_refused_whatever_it_is_called_ls7(kb_root: Path) -> None:
    """`_looks_like_text` rejects NULs, and `detect_kind` short-circuits before reaching it (LS-7).

    The same bytes were refused when the file had no extension and accepted when it was called
    `notes.txt`, which is the shape a real source arrives in.
    """
    source = kb_root.parent / "notes.txt"
    source.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00")

    with pytest.raises(MalformedSourceError):
        extract(source)


def test_a_byte_order_mark_does_not_swallow_the_first_heading_ls10(kb_root: Path) -> None:
    """`utf-8-sig` has to be tried before `utf-8`, which never fails on a BOM (LS-7, LS-10).

    The mark survived into the text, so `^#` no longer matched: the opening chapter was folded into
    `(front matter)` and the document title degraded to the filename. That chapter is the stable key
    LS-10 reconciles on, so a later pass reading the same book from a BOM-free path would see a
    chapter that had never existed and file it as a pure addition.
    """
    body = "# Radical Candor\n\nThe thesis.\n\n## Challenge directly\n\nBody two.\n"
    plain = kb_root.parent / "plain.md"
    plain.write_bytes(body.encode("utf-8"))
    marked = kb_root.parent / "marked.md"
    marked.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))

    assert extract(marked).title == extract(plain).title == "Radical Candor"
    assert [s.title for s in extract(marked).sections] == [s.title for s in extract(plain).sections]


def test_an_epub_chapter_is_decoded_by_its_own_declaration_ls7(kb_root: Path) -> None:
    """`payload.decode("utf-8", errors="replace")` discards the XML declaration (LS-7).

    A chapter declared ISO-8859-1 reached the expert as `Kierkegaardï¿½s rï¿½sumï¿½` — silent,
    permanent (Layer 1 never deletes), and applied to every book that is not in English.
    """
    chapter = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Le caf\xe9 de Bo\xefto</h1><p>Kierkegaard\xe9s r\xe9sum\xe9 of the argument.</p>"
        "</body></html>"
    ).encode("latin-1")
    path = kb_root.parent / "cafe.epub"
    path.write_bytes(_epub_with_chapter(chapter))

    extracted = extract(path)

    assert "�" not in extracted.sections[0].text
    assert "Le café de Boïto" in extracted.sections[0].text
    assert "Kierkegaardés résumé" in extracted.sections[0].text


def _epub_with_chapter(chapter: bytes) -> bytes:
    """A minimal conformant EPUB 3 holding exactly one chapter document."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/book.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/book.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Café</dc:title>'
            "</metadata><manifest>"
            '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        )
        archive.writestr("OEBPS/c1.xhtml", chapter)
    return buffer.getvalue()


def test_a_short_outline_title_anchors_at_a_line_start_not_inside_prose_ls10(kb_root: Path) -> None:
    """`page_text.find(title)` puts chapter 6's anchor inside chapter 5's prose (LS-10).

    Bare numerals and Roman numerals are ordinary outline titles in typeset books, and several
    chapters share a page. The expert was then asked what chapter 6 argues while reading the tail of
    chapter 5, chapter 5 kept one sentence, and `warnings` came back empty because
    `_drop_colliding_anchors` only fires on exactly equal offsets.
    """
    pages = [
        [
            ("5", 14.0, 700.0),
            ("Ship early. In the 6 months after launch we learned more.", 11.0, 680.0),
            ("6", 14.0, 640.0),
            ("Measure honestly. A metric nobody acts on is decoration.", 11.0, 620.0),
        ]
    ]
    path = kb_root.parent / "numbered.pdf"
    path.write_bytes(add_outline(make_pdf(pages), [("5", 1, 0), ("6", 1, 0)]))

    extracted = extract(path)

    sections = {section.title: section.text for section in extracted.sections}
    assert "Ship early" in sections["5"]
    assert "6 months after launch" in sections["5"], "chapter 5 keeps its whole body"
    assert sections["6"].startswith("Measure honestly")


def test_a_url_pointing_back_inside_the_machine_is_refused_ls7() -> None:
    """`origin` is model-chosen, and a fetch is a read the knowledge base then keeps (LS-7).

    Nothing restricted the host, so a prompt-injected turn could reach cloud metadata or a service
    on loopback, put the response in front of the model, and copy it into a topic as a reference.
    """
    for blocked in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/admin",
        "http://localhost/secrets",
        "http://10.0.0.5/internal",
        "file:///etc/passwd",
    ):
        with pytest.raises((FetchError, UnsupportedSourceError)):
            check_fetchable(blocked)

    check_fetchable("https://example.com/book.pdf")


def test_a_section_title_is_one_line_however_the_source_wrote_it_ls10() -> None:
    """The file format, the reading record and the resume frontier all assume one line (LS-10).

    A pretty-printed EPUB `navLabel` wraps its text and PDF bookmarks carry whatever the typesetter
    put in them. Left alone, such a section grew a duplicate heading block in the file on every pass
    and a duplicate reading-record entry on every write, with `validate_tree` reporting nothing.
    """
    section = Section(
        title="Chapter 1: Care Personally\n        and Challenge Directly", level=1, text="x"
    )

    assert section.title == "Chapter 1: Care Personally and Challenge Directly"
