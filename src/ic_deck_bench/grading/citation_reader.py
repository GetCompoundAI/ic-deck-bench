import json
import xml.etree.ElementTree as ET

from ic_deck_bench.grading.citations import NativeCitation
from ic_deck_bench.office.deliverable_checks.deck import (
    NS_A,
    open_deck,
    ordered_slide_names,
)

CITATIONS_PART = "customXml/citations.json"
SAVED_VERSION = 4
PPTX_CITATION_EXT_URI = "http://schemas.getcompound.ai/office/citations-inline"
_CITATION_NS = "{http://schemas.getcompound.ai/office}"

Json = dict[str, object]


def _dict(value: object) -> Json:
    return (
        {str(key): one for key, one in value.items()} if isinstance(value, dict) else {}
    )


def _list(value: object) -> list[Json]:
    return (
        [_dict(one) for one in value if isinstance(one, dict)]
        if isinstance(value, list)
        else []
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _number(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _strings(node: object) -> tuple[str, ...]:
    if isinstance(node, str):
        return (node,)
    if isinstance(node, dict):
        return tuple(text for value in node.values() for text in _strings(value))
    if isinstance(node, list):
        return tuple(text for value in node for text in _strings(value))
    return ()


def _fields(node: object, name: str = "") -> tuple[tuple[str, str], ...]:
    if isinstance(node, bool):
        return ()
    if isinstance(node, str | int | float):
        return ((name, str(node)),)
    if isinstance(node, dict):
        return tuple(
            field for key, value in node.items() for field in _fields(value, str(key))
        )
    if isinstance(node, list):
        return tuple(field for value in node for field in _fields(value, name))
    return ()


def _held(arm: Json) -> list[Json]:
    return [_dict(arm["passage"])] if "passage" in arm else _list(arm.get("passages"))


def _quote(arm: Json, held: list[Json]) -> str:
    return _text(arm.get("quote")) or "\n".join(
        _text(one.get("text")) for one in held if one.get("text")
    )


def _located(kind: str, arm: Json) -> tuple[str, str, int, str]:
    if kind == "drive_file":
        return _text(_dict(arm.get("file")).get("path")), "", 0, ""
    if kind == "sheet_range":
        return _text(_dict(arm.get("sheet")).get("path")), _text(arm.get("a1")), 0, ""
    if kind == "doc_chunk":
        return (
            _text(_dict(arm.get("chunk")).get("path")),
            "",
            0,
            _text(arm.get("quote")),
        )
    if kind == "deck_slide":
        slide = _dict(arm.get("slide"))
        return (
            _text(slide.get("path")),
            "",
            _number(slide.get("number")),
            _text(arm.get("quote")),
        )
    if kind == "pdf_page":
        held = _held(arm)
        page = _dict(arm.get("page"))
        first = _dict(held[0].get("page")) if held else page
        return (
            _text(page.get("path")) or _text(first.get("path")),
            "",
            _number(page.get("number")) or _number(first.get("number")),
            _quote(arm, held),
        )
    if kind == "web_page":
        held = _held(arm)
        url = _text(arm.get("url")) or (_text(held[0].get("url")) if held else "")
        return url, "", 0, _quote(arm, held)
    if kind in ("sec_filing", "earnings_transcript"):
        return "", "", 0, _quote(arm, _held(arm))
    if kind == "text_lines":
        lines = _list(arm.get("lines"))
        path = _text(arm.get("path")) or (_text(lines[0].get("path")) if lines else "")
        quote = _text(arm.get("quote")) or "\n".join(
            _text(one.get("text")) for one in lines
        )
        return path, "", 0, quote
    return "", "", 0, ""


def _saved_citation(saved: Json) -> NativeCitation | None:
    locator = saved.get("locator")
    if (
        saved.get("version") != SAVED_VERSION
        or not isinstance(saved.get("key"), str)
        or not isinstance(locator, dict)
        or len(locator) != 1
        or not isinstance(saved.get("description"), dict)
    ):
        return None
    ((kind, arm),) = _dict(locator).items()
    path, a1, page, quote = _located(kind, _dict(arm))
    return NativeCitation(
        key=_text(saved.get("key")),
        kind=kind,
        file_id=_text(saved.get("file_id")),
        described=tuple(
            (name, str(value))
            for name, value in _dict(saved.get("description")).items()
        ),
        path=path,
        a1=a1,
        page=page or None,
        quote=quote,
        locator_text=_strings(arm),
        locator_fields=_fields(arm),
    )


def _agent_citation(body: Json) -> NativeCitation | None:
    path = body.get("filename")
    if not isinstance(path, str):
        return None
    return NativeCitation(
        key="",
        kind="agent_events",
        file_id=_text(body.get("file_id")),
        described=tuple(
            (name, value)
            for name, value in (
                ("title", _text(body.get("title"))),
                ("source", _text(body.get("source_label"))),
            )
            if value
        ),
        path=path,
        a1=_text(body.get("cell_range")),
        page=None,
        quote="",
        locator_text=_strings(body),
    )


def _sources_read(body: Json) -> tuple[NativeCitation, ...]:
    sources = body.get("sources")
    if not isinstance(sources, list):
        one = _agent_citation(body)
        return (one,) if one is not None else ()
    read = (_saved_citation(_dict(one)) for one in sources if isinstance(one, dict))
    return tuple(one for one in read if one is not None)


def _citation_bodies(payload: object) -> dict[str, Json]:
    return {
        _text(entry.get("id")): _dict(entry.get("citation"))
        for entry in _list(_dict(payload).get("citations"))
        if isinstance(entry.get("id"), str) and isinstance(entry.get("citation"), dict)
    }


def slide_citations(data: bytes) -> dict[int, tuple[NativeCitation, ...]]:
    deck = open_deck(data)
    if deck is None:
        return {}
    with deck:
        if CITATIONS_PART not in deck.namelist():
            return {}
        try:
            bodies = _citation_bodies(json.loads(deck.read(CITATIONS_PART)))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if not bodies:
            return {}
        out: dict[int, tuple[NativeCitation, ...]] = {}
        for number, part in enumerate(ordered_slide_names(deck), start=1):
            try:
                root = ET.fromstring(deck.read(part))
            except (ET.ParseError, KeyError):
                continue
            found: list[NativeCitation] = []
            for ext in root.iter(f"{{{NS_A}}}ext"):
                if ext.get("uri") != PPTX_CITATION_EXT_URI:
                    continue
                for cite in ext.iter(f"{_CITATION_NS}cite"):
                    body = bodies.get(cite.get("id") or "")
                    if body is not None:
                        found.extend(_sources_read(body))
            if found:
                out[number] = tuple(found)
    return out
