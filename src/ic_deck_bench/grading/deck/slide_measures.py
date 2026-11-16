import re
from collections import Counter
from dataclasses import dataclass

from ic_deck_bench.grading.deck.drawings import Drawing, Paragraph, SlideDrawing
from ic_deck_bench.grading.deck.pptx import (
    DEFAULT_SLIDE_SIZE_EMU,
    chart_kind,
    chart_part_xml,
    presentation_slide_size,
)

BODY_TOP = 0.18
FOOTER_TOP = 0.85
COVER_WORDS = 30
DIVIDER_WORDS = 12
AGENDA_HEADINGS = frozenset({"agenda", "contents", "table of contents"})
AGENDA_WORDS = 40
WALL_WORDS = 40
LAYOUT_COLUMNS = 3
LAYOUT_ROWS = 3
CELL_COVER = 0.3
TEXT_BLOCK_WORDS = 4
DIAGRAM_MARKS = 6
MARK_AREA = 0.05
PANEL_AREA = 0.03
PASTED_AREA = 0.3
KPI_POINTS = 20.0
KPI_WORDS = 4
KPI_TILES = 2
DIAGRAM_LINKS = 3
DRAWN_BARS = 4
DRAWN_BAR_SPREAD = 1.5
SECTION_LAYOUTS = frozenset({"secHead", "title"})
SECTION_LAYOUT_WORDS = 40
SUBTITLE_WORDS = 30
_SECTION_NUMBER = re.compile(
    r"^(?:(?:section|part|chapter)\s+)?(?:\d{1,2}|[ivx]{1,4})\.?$", re.IGNORECASE
)
_BAR_GEOMETRY = frozenset({"rect", ""})
_EDGE = 0.02


def _steps(left: Drawing, right: Drawing) -> bool:
    return (
        abs(left.y + left.h - right.y) <= _EDGE
        or abs(right.y + right.h - left.y) <= _EDGE
    )


_LINK_GEOMETRY = re.compile(r"arrow|chevron|homeplate|brace|bracket", re.IGNORECASE)


def body_paragraphs(slide: SlideDrawing) -> list[Paragraph]:
    paragraphs = [one for one in slide.paragraphs if not one.title]
    shaped = [one for one in paragraphs if not one.in_table]
    if len(paragraphs) == len(slide.paragraphs) and shaped:
        heading_top = min(one.top for one in shaped)
        if heading_top < BODY_TOP:
            paragraphs = [
                one for one in paragraphs if one.in_table or one.top > heading_top
            ]
    return [one for one in paragraphs if one.top < FOOTER_TOP]


def _prose_words(slide: SlideDrawing) -> int:
    return sum(one.words for one in body_paragraphs(slide) if not one.in_table)


def is_cover(slide: SlideDrawing) -> bool:
    return (
        slide.number == 1
        and _prose_words(slide) < COVER_WORDS
        and not any(one.kind in ("chart", "table") for one in slide.drawings)
    )


def _says_agenda(paragraphs: list[Paragraph]) -> bool:
    return any(
        one.text.rstrip(":").strip().lower() in AGENDA_HEADINGS for one in paragraphs
    )


def agenda_numbers(slides: list[SlideDrawing]) -> set[int]:
    found: set[int] = set()
    untitled: dict[tuple[str, ...], list[int]] = {}
    for slide in slides:
        if sum(one.words for one in body_paragraphs(slide)) > AGENDA_WORDS or any(
            one.kind in ("chart", "picture") for one in slide.drawings
        ):
            continue
        titled = [one for one in slide.paragraphs if one.title]
        if titled:
            if _says_agenda(titled):
                found.add(slide.number)
            continue
        headings: list[Paragraph] = []
        opened: set[int] = set()
        for one in slide.paragraphs:
            if not one.in_table and one.top < BODY_TOP and one.block not in opened:
                headings.append(one)
            opened.add(one.block)
        if _says_agenda(headings):
            sections = tuple(one.text.lower() for one in body_paragraphs(slide))
            untitled.setdefault(sections, []).append(slide.number)
    for numbers in untitled.values():
        if len(numbers) > 1:
            found.update(numbers)
    return found


def is_divider(slide: SlideDrawing) -> bool:
    return (
        not is_cover(slide)
        and _prose_words(slide) <= DIVIDER_WORDS
        and not any(one.kind in ("chart", "table", "picture") for one in slide.drawings)
    )


def _section_number(paragraph: Paragraph) -> bool:
    return bool(_SECTION_NUMBER.match(paragraph.text.strip()))


def is_section_page(slide: SlideDrawing) -> bool:
    if any(
        one.kind in ("chart", "table", "picture") for one in slide.drawings
    ) or drawn_chart(list(slide.drawings)):
        return False
    shown = [
        one
        for one in slide.paragraphs
        if one.words and not one.in_table and one.top < FOOTER_TOP
    ]
    numbered = any(_section_number(one) for one in shown)
    blocks: dict[int, list[Paragraph]] = {}
    for one in shown:
        if not _section_number(one):
            blocks.setdefault(one.block, []).append(one)
    if not blocks or len(blocks) > 2:
        return False
    if slide.layout_type in SECTION_LAYOUTS:
        return _prose_words(slide) <= SECTION_LAYOUT_WORDS and all(
            sum(one.words for one in said) <= SUBTITLE_WORDS for said in blocks.values()
        )
    titled = [block for block, said in blocks.items() if any(one.title for one in said)]
    heading = (
        titled[0]
        if titled
        else max(blocks, key=lambda block: max(one.shown_size for one in blocks[block]))
    )
    if any(sum(one.words for one in said) > SUBTITLE_WORDS for said in blocks.values()):
        return False
    return numbered or min(one.top for one in blocks[heading]) >= BODY_TOP


def content_slides(slides: list[SlideDrawing]) -> list[SlideDrawing]:
    agendas = agenda_numbers(slides)
    return [
        one
        for one in slides
        if not one.hidden
        and not is_cover(one)
        and not is_divider(one)
        and one.number not in agendas
    ]


def visible_slides(slides: list[SlideDrawing]) -> list[SlideDrawing]:
    visible = []
    for slide in content_slides(slides):
        body = [one for one in slide.drawings if _in_body(one)]
        pictures = [one for one in body if one.kind == "picture"]
        pasted = (
            len(pictures) == 1
            and pictures[0].area >= PASTED_AREA
            and not any(
                one.kind in ("chart", "table") or (one.kind == "shape" and one.texted)
                for one in body
            )
        )
        if not pasted:
            visible.append(slide)
    return visible


def walls(slide: SlideDrawing) -> list[list[Paragraph]]:
    blocks: dict[int, list[Paragraph]] = {}
    for one in body_paragraphs(slide):
        if not one.in_table:
            blocks.setdefault(one.block, []).append(one)
    return [
        block
        for block in blocks.values()
        if sum(one.words for one in block) >= WALL_WORDS
    ]


def scannable(wall: list[Paragraph]) -> bool:
    return (
        any(one.selectively_bold for one in wall)
        or len({(one.level, one.margin) for one in wall}) > 1
    )


def drawn_chart(drawings: list[Drawing]) -> str | None:
    found = drawn_chart_bars(drawings)
    return found[0] if found else None


def drawn_chart_bars(drawings: list[Drawing]) -> tuple[str, list[Drawing]] | None:
    bars = [
        one
        for one in drawings
        if one.kind == "shape"
        and not one.texted
        and one.geometry in _BAR_GEOMETRY
        and one.w > 0
        and one.h > 0
    ]

    def of_one(size: float, others: list[float]) -> bool:
        return all(abs(other - size) <= 0.1 * size for other in others)

    def spread(lengths: list[float]) -> bool:
        return max(lengths) >= DRAWN_BAR_SPREAD * min(lengths)

    for one in bars:
        columns = sorted(
            (
                other
                for other in bars
                if of_one(one.w, [other.w])
                and other.y < one.y + one.h + 0.3
                and other.y + other.h > one.y - 0.3
            ),
            key=lambda other: other.x,
        )
        apart = [
            column
            for index, column in enumerate(columns)
            if index == 0 or column.x >= columns[index - 1].x + 0.9 * column.w
        ]
        if len(apart) >= DRAWN_BARS and spread([column.h for column in apart]):
            bottoms = [column.y + column.h for column in apart]
            tops = [column.y for column in apart]
            if max(bottoms) - min(bottoms) <= _EDGE:
                return ("bar", apart)
            pairs = list(zip(apart, apart[1:]))
            if max(tops) - min(tops) > _EDGE and sum(
                _steps(left, right) for left, right in pairs
            ) * 2 >= len(pairs):
                return ("waterfall", apart)
        rows = sorted(
            (
                other
                for other in bars
                if of_one(one.h, [other.h]) and abs(other.x - one.x) <= 0.01
            ),
            key=lambda other: other.y,
        )
        stacked = [
            row
            for index, row in enumerate(rows)
            if index == 0 or row.y >= rows[index - 1].y + 0.9 * row.h
        ]
        if len(stacked) >= DRAWN_BARS and spread([row.w for row in stacked]):
            return ("bar", stacked)
    return None


MEKKO_COLUMNS = 3


def _stack_top(base: Drawing, boxes: list[Drawing]) -> float:
    top = base.y
    while True:
        above = [
            one
            for one in boxes
            if abs(one.x - base.x) <= _EDGE / 2
            and abs(one.w - base.w) <= _EDGE / 2
            and abs(one.y + one.h - top) <= _EDGE
            and one.y < top
        ]
        if not above:
            return top
        top = min(one.y for one in above)


def drawn_mekko(drawings: list[Drawing], bars: list[Drawing]) -> list[Drawing]:
    boxes = [
        one
        for one in drawings
        if one.kind == "shape" and one.geometry in _BAR_GEOMETRY and one.w > 0
    ]

    def same_column(one: Drawing, other: Drawing) -> bool:
        return abs(one.x - other.x) <= _EDGE / 2 and abs(one.w - other.w) <= _EDGE / 2

    if not bars:
        return []
    baseline = max(
        one.y + one.h for bar in bars for one in boxes if same_column(one, bar)
    )
    bottom_row = [one for one in boxes if abs(one.y + one.h - baseline) <= _EDGE]
    runs: list[list[Drawing]] = []
    for one in sorted(bottom_row, key=lambda one: one.x):
        if runs and one.x < runs[-1][-1].x + 0.9 * runs[-1][-1].w:
            continue
        if runs and one.x - (runs[-1][-1].x + runs[-1][-1].w) <= _EDGE:
            runs[-1].append(one)
        else:
            runs.append([one])
    holding = [
        run
        for run in runs
        if all(any(same_column(column, bar) for column in run) for bar in bars)
    ]
    if not holding or len(holding[0]) < MEKKO_COLUMNS:
        return []
    [columns, *_] = holding
    tops = [_stack_top(column, boxes) for column in columns]
    widths = [column.w for column in columns]
    if max(tops) - min(tops) > _EDGE or max(widths) < DRAWN_BAR_SPREAD * min(widths):
        return []
    return [
        one
        for one in boxes
        if any(same_column(one, column) for column in columns)
        and one.y >= min(tops) - _EDGE
        and one.y + one.h <= baseline + _EDGE
    ]


def drawn_gantt(
    drawings: list[Drawing], bars: list[Drawing], slide_aspect: float
) -> list[Drawing]:
    if not bars or any(
        abs(one.x - bars[0].x) > 0.01 or abs(one.h - bars[0].h) > 0.1 * bars[0].h
        for one in bars
    ):
        return []
    height = bars[0].h
    extent = max(one.y + one.h for one in bars) - min(one.y for one in bars)
    top = min(one.y for one in bars) - extent
    bottom = max(one.y + one.h for one in bars) + extent
    floating = [
        one
        for one in drawings
        if one.kind == "shape"
        and not one.texted
        and one.geometry in _BAR_GEOMETRY
        and abs(one.h - height) <= 0.1 * height
        and top <= one.y + one.h / 2 <= bottom
        and abs(one.x - bars[0].x) > 0.01
    ]
    later = [
        one
        for one in floating
        if all(abs(one.y - bar.y) > height / 2 for bar in bars)
        and one.w * slide_aspect >= 2 * one.h
    ]
    return floating if later else []


def drawn_instead_of_native(
    drawings: list[Drawing], slide_aspect: float
) -> tuple[str, list[Drawing]] | None:
    left = list(drawings)
    while (found := drawn_chart_bars(left)) is not None:
        _, bars = found
        exempt = drawn_mekko(left, bars) or drawn_gantt(
            left, bars, slide_aspect=slide_aspect
        )
        if not exempt:
            return found
        cleared = [*exempt, *bars]
        left = [one for one in left if all(one is not box for box in cleared)]
    return None


def _in_body(drawing: Drawing) -> bool:
    return BODY_TOP <= drawing.y + drawing.h / 2 <= FOOTER_TOP


def layout_blocks(slide: SlideDrawing) -> list[tuple[str, Drawing]]:
    words: Counter[int] = Counter()
    for one in slide.paragraphs:
        if not one.title and not one.in_table:
            words[one.block] += one.words
    blocks: list[tuple[str, Drawing]] = []
    marks: list[Drawing] = []
    for index, drawing in enumerate(slide.drawings, start=1):
        if not _in_body(drawing):
            continue
        if drawing.kind in ("chart", "table", "picture"):
            blocks.append((drawing.kind, drawing))
        elif drawing.kind == "shape" and words[index] >= TEXT_BLOCK_WORDS:
            blocks.append(("text", drawing))
        elif drawing.kind == "connector" or (
            drawing.kind == "shape" and drawing.area < MARK_AREA
        ):
            marks.append(drawing)
    if len(marks) >= DIAGRAM_MARKS:
        left, top = (min(one.x for one in marks), min(one.y for one in marks))
        right = max(one.x + one.w for one in marks)
        bottom = max(one.y + one.h for one in marks)
        blocks.append(
            (
                "diagram",
                Drawing(
                    kind="shape",
                    geometry="",
                    texted=False,
                    x=left,
                    y=top,
                    w=right - left,
                    h=bottom - top,
                ),
            )
        )
    return blocks


def _covers(drawing: Drawing, column: int, row: int) -> bool:
    width = 1 / LAYOUT_COLUMNS
    height = (FOOTER_TOP - BODY_TOP) / LAYOUT_ROWS
    left, top = (column * width, BODY_TOP + row * height)
    across = min(drawing.x + drawing.w, left + width) - max(drawing.x, left)
    down = min(drawing.y + drawing.h, top + height) - max(drawing.y, top)
    return max(across, 0.0) * max(down, 0.0) >= CELL_COVER * width * height


@dataclass(frozen=True, slots=True)
class Layout:
    columns: tuple[tuple[str, ...], ...]
    panels: int

    def __str__(self) -> str:
        thirds = " | ".join("+".join(one) or "empty" for one in self.columns)
        return f"{thirds} ({self.panels} panels)"


def layout(slide: SlideDrawing) -> Layout:
    blocks = layout_blocks(slide)
    return Layout(
        columns=tuple(
            tuple(
                sorted(
                    {
                        kind
                        for (kind, drawing) in blocks
                        if any(
                            _covers(drawing, column, row) for row in range(LAYOUT_ROWS)
                        )
                    }
                )
            )
            for column in range(LAYOUT_COLUMNS)
        ),
        panels=sum(
            1
            for (kind, drawing) in blocks
            if kind in ("chart", "table", "picture") and drawing.area >= PANEL_AREA
        ),
    )


def kpi_points(raw: bytes) -> float:
    width, _ = presentation_slide_size(raw) or DEFAULT_SLIDE_SIZE_EMU
    return KPI_POINTS * min(1.0, width / DEFAULT_SLIDE_SIZE_EMU[0])


def exhibit_kinds(raw: bytes, slides: list[SlideDrawing]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    least = kpi_points(raw)
    for slide in content_slides(slides):
        body = [
            (index, drawing)
            for index, drawing in enumerate(slide.drawings, start=1)
            if _in_body(drawing)
        ]
        kinds: set[str] = set()
        for _, drawing in body:
            if drawing.kind == "chart":
                xml = (
                    chart_part_xml(raw, drawing.chart_part)
                    if drawing.chart_part
                    else None
                )
                kinds.add("chart" if xml is None else chart_kind(xml))
            elif drawing.kind in ("table", "picture"):
                kinds.add(drawing.kind)
        headline = {
            one.block
            for one in slide.paragraphs
            if not one.title
            and not one.in_table
            and one.shown_size >= least
            and one.words <= KPI_WORDS
        }
        tiles = [
            index
            for (index, drawing) in body
            if drawing.kind == "shape" and index in headline
        ]
        if len(tiles) >= KPI_TILES:
            kinds.add("kpi tiles")
        links = [
            drawing
            for (_, drawing) in body
            if drawing.kind == "connector"
            or _LINK_GEOMETRY.search(drawing.geometry) is not None
        ]
        if len(links) >= DIAGRAM_LINKS:
            kinds.add("diagram")
        drawn = drawn_chart([drawing for (_, drawing) in body])
        if drawn is not None:
            kinds.add(drawn)
        for kind in kinds:
            found.setdefault(kind, []).append(slide.number)
    return found


OFF_SLIDE_TOLERANCE = 0.005
TOUCH_TOLERANCE = 0.005
FRAME_MARGIN = 0.05
OVERLAP_SHARE = 0.05
OVERLAID_LABEL_WORDS = 12
LINE_WORDS = 2
CALLOUT_SHARE = 0.25
BACKGROUND_AREA = 0.8
_FOOTER_PLACEHOLDERS = frozenset({"ftr", "sldNum", "dt"})
_TITLE_PLACEHOLDERS = frozenset({"title", "ctrTitle"})

Box = tuple[float, float, float, float]


def _edges(box: Box) -> Box:
    x, y, w, h = box
    return (x, y, x + w, y + h)


def _overlap(one: Box, other: Box) -> float:
    left, top, right, bottom = _edges(one)
    other_left, other_top, other_right, other_bottom = _edges(other)
    across = min(right, other_right) - max(left, other_left)
    down = min(bottom, other_bottom) - max(top, other_top)
    return max(across, 0.0) * max(down, 0.0)


def _area(boxes: tuple[Box, ...]) -> float:
    return sum(one[2] * one[3] for one in boxes)


def _collide(lines: tuple[Box, ...], frame: Box) -> bool:
    x, y, w, h = frame
    frame = (
        x + TOUCH_TOLERANCE,
        y + TOUCH_TOLERANCE,
        w - 2 * TOUCH_TOLERANCE,
        h - 2 * TOUCH_TOLERANCE,
    )
    smaller = min(_area(lines), max(frame[2], 0.0) * max(frame[3], 0.0))
    covered = sum(_overlap(one, frame) for one in lines)
    return smaller > 0 and covered > OVERLAP_SHARE * smaller


def _box(one: Drawing) -> Box:
    return (one.x, one.y, one.w, one.h)


def _shown_box(one: Drawing) -> Box:
    if one.kind == "table":
        return one.content or _box(one)
    if one.kind not in ("picture", "chart"):
        return _box(one)
    x, y, w, h = _box(one)
    return (
        x + FRAME_MARGIN * w,
        y + FRAME_MARGIN * h,
        w * (1 - 2 * FRAME_MARGIN),
        h * (1 - 2 * FRAME_MARGIN),
    )


def _upright(one: Drawing) -> bool:
    return one.rotation % 180 == 0


def _is_exhibit(one: Drawing) -> bool:
    return _upright(one) and (one.kind == "picture" or one.element == "graphicFrame")


def _is_background(one: Drawing) -> bool:
    return one.kind == "picture" and one.area >= BACKGROUND_AREA


def _bleeds(one: Drawing) -> bool:
    left, top, right, bottom = _edges(_box(one))
    across = left <= OFF_SLIDE_TOLERANCE and right >= 1 - OFF_SLIDE_TOLERANCE
    down = top <= OFF_SLIDE_TOLERANCE and bottom >= 1 - OFF_SLIDE_TOLERANCE
    return across or down


def _shows_text(one: Drawing) -> bool:
    return (
        one.kind == "shape"
        and (one.words > 0 or one.placeholder in _FOOTER_PLACEHOLDERS)
        and bool(one.lines)
        and _upright(one)
    )


def _label(one: Drawing) -> str:
    kind = "text" if one.kind == "shape" and one.words else one.kind
    if kind == "shape":
        kind = "frame"
    named = f" '{one.name}'" if one.name else ""
    left, top, right, bottom = _edges(_box(one))
    return f"{kind}{named} at ({left:.2f}, {top:.2f})-({right:.2f}, {bottom:.2f})"


def _past_edges(box: Box) -> str:
    left, top, right, bottom = _edges(box)
    if right <= 0 or bottom <= 0 or left >= 1 or top >= 1:
        return ""
    overhang = {"left": -left, "top": -top, "right": right - 1, "bottom": bottom - 1}
    edge, amount = max(overhang.items(), key=lambda one: one[1])
    return f"{amount:.1%} past the {edge} edge" if amount > OFF_SLIDE_TOLERANCE else ""


def layout_defects(slide: SlideDrawing) -> list[str]:
    drawings = list(slide.drawings)
    exhibits = [
        (index, one)
        for (index, one) in enumerate(drawings)
        if _is_exhibit(one) and not _is_background(one)
    ]
    texts = [(index, one) for (index, one) in enumerate(drawings) if _shows_text(one)]
    found: list[str] = []
    for _, one in exhibits:
        past = "" if _bleeds(one) else _past_edges(_shown_box(one))
        if past:
            found.append(f"off the slide: {_label(one)} runs {past}")
    for _, one in texts:
        past = _past_edges(one.content or _box(one))
        if past:
            found.append(f"off the slide: {_label(one)} runs {past}")
    collided: set[tuple[int, int]] = set()
    for exhibit_index, exhibit in exhibits:
        shown = _shown_box(exhibit)
        for text_index, text in texts:
            if text_index > exhibit_index or text.words <= OVERLAID_LABEL_WORDS:
                continue
            if (
                exhibit.kind == "chart"
                and _area(text.lines) <= CALLOUT_SHARE * exhibit.area
                and all(
                    _overlap(one, _box(exhibit)) >= one[2] * one[3]
                    for one in text.lines
                )
            ):
                continue
            if _collide(text.lines, shown):
                collided.add((exhibit_index, text_index))
                found.append(f"overlap: {_label(exhibit)} covers {_label(text)}")
    charts = [_box(one) for (_, one) in exhibits if one.kind == "chart"]
    footers = [
        (index, one)
        for (index, one) in texts
        if one.placeholder in _FOOTER_PLACEHOLDERS
        or (
            one.words >= LINE_WORDS
            and one.lines[0][1] >= FOOTER_TOP
            and not any(
                all(_overlap(line, chart) >= line[2] * line[3] for line in one.lines)
                for chart in charts
            )
        )
    ]
    footer_indexes = {index for (index, _) in footers}
    body: list[tuple[int, Drawing, tuple[Box, ...]]] = [
        (index, one, (_shown_box(one),)) for (index, one) in exhibits
    ] + [
        (index, one, one.lines)
        for (index, one) in texts
        if index not in footer_indexes
        and one.placeholder not in _TITLE_PLACEHOLDERS
        and one.words >= LINE_WORDS
    ]
    for body_index, one, boxes in body:
        if max(box[1] + box[3] for box in boxes) <= FOOTER_TOP:
            continue
        for footer_index, footer in footers:
            pair = (min(body_index, footer_index), max(body_index, footer_index))
            if pair in collided or (body_index, footer_index) in collided:
                continue
            if not any(_collide(footer.lines, box) for box in boxes):
                continue
            collided.add(pair)
            found.append(f"footer: {_label(one)} runs into {_label(footer)}")
    return found
