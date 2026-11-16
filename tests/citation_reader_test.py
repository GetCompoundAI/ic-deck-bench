import io
import json
import zipfile

from ic_deck_bench.grading.citations import pptx_slide_citations

_PATH = "Project Voltaic Data Room/11 Transaction Materials/11.04 QoE Report.pdf"
_SLIDE = (
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:c="http://schemas.getcompound.ai/office"><a:extLst>'
    '<a:ext uri="http://schemas.getcompound.ai/office/citations-inline">'
    '<c:cite id="{cite}"/></a:ext></a:extLst></p:sld>'
)


def _deck(source: dict[str, object]) -> bytes:
    citations = {
        "version": 1,
        "citations": [{"id": "c1", "citation": {"sources": [source]}}],
    }
    written = io.BytesIO()
    with zipfile.ZipFile(written, "w") as deck:
        deck.writestr("customXml/citations.json", json.dumps(citations))
        deck.writestr("ppt/slides/slide1.xml", _SLIDE.format(cite="c0"))
        deck.writestr("ppt/slides/slide2.xml", _SLIDE.format(cite="c1"))
    return written.getvalue()


def _source(version: int) -> dict[str, object]:
    return {
        "version": version,
        "key": "k1",
        "file_id": _PATH,
        "description": {"shows": "QoE summary"},
        "locator": {"pdf_page": {"passage": {"page": {"path": _PATH, "number": 4}}}},
    }


def test_a_slide_citation_names_its_data_room_document() -> None:
    cited = pptx_slide_citations(_deck(_source(4)))
    assert ([2], _PATH, 4) == (list(cited), cited[2][0].path, cited[2][0].page)


def test_a_citation_saved_at_another_version_is_not_read() -> None:
    assert {} == pptx_slide_citations(_deck(_source(3)))
