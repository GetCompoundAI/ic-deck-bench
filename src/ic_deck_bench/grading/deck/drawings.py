import math
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

from ic_deck_bench.grading.deck.pptx import (
    DEFAULT_SLIDE_SIZE_EMU,
    first_rel_target,
    presentation_slide_size,
    slide_scheme,
    solid_fill_hex,
    theme_colors,
)
from ic_deck_bench.office.deliverable_checks.deck import (
    NS_A,
    NS_P,
    frame_chart_rid,
    open_deck,
    ordered_slide_names,
    part_rels,
)

NS_MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"


@dataclass(frozen=True, slots=True)
class Paragraph:
    words: int
    bold_words: int
    size: float
    title: bool
    top: float
    in_table: bool = False
    block: int = 0
    level: int = 0
    margin: int = 0
    text: str = ""
    italic_words: int = 0
    inherited_size: float = 0.0
    drawn_size: float = 0.0
    run_fills: tuple[tuple[str, str | None], ...] = ()

    @property
    def shown_size(self) -> float:
        return self.drawn_size or self.size

    @property
    def selectively_bold(self) -> bool:
        return 0 < self.bold_words < self.words


@dataclass(frozen=True, slots=True)
class Drawing:
    kind: str
    geometry: str
    texted: bool
    x: float
    y: float
    w: float
    h: float
    chart_part: str = ""
    element: str = ""
    name: str = ""
    placeholder: str = ""
    rotation: float = 0.0
    words: int = 0
    lines: tuple[tuple[float, float, float, float], ...] = ()
    fill: str | None = None

    @property
    def content(self) -> tuple[float, float, float, float] | None:
        if not self.lines:
            return None
        left = min(one[0] for one in self.lines)
        top = min(one[1] for one in self.lines)
        right = max(one[0] + one[2] for one in self.lines)
        bottom = max(one[1] + one[3] for one in self.lines)
        return (left, top, right - left, bottom - top)

    @property
    def area(self) -> float:
        return self.w * self.h

    def intersects(self, other: "Drawing") -> bool:
        return min(self.x + self.w, other.x + other.w) > max(self.x, other.x) and min(
            self.y + self.h, other.y + other.h
        ) > max(self.y, other.y)

    def centred_in(self, other: "Drawing") -> bool:
        centre_x, centre_y = (self.x + self.w / 2, self.y + self.h / 2)
        return (
            other.x <= centre_x <= other.x + other.w
            and other.y <= centre_y <= other.y + other.h
        )


@dataclass(frozen=True)
class SlideDrawing:
    number: int
    paragraphs: tuple[Paragraph, ...]
    drawings: tuple[Drawing, ...]
    hidden: bool = False
    layout_type: str = ""


@dataclass(frozen=True, slots=True)
class _Placement:
    x_scale: float = 1.0
    x_offset: float = 0.0
    y_scale: float = 1.0
    y_offset: float = 0.0


def _xfrm_box(
    xfrm: ET.Element | None, off_tag: str = "off", ext_tag: str = "ext"
) -> tuple[int, int, int, int] | None:
    if xfrm is None:
        return None
    off = xfrm.find(f"{{{NS_A}}}{off_tag}")
    ext = xfrm.find(f"{{{NS_A}}}{ext_tag}")
    if off is None or ext is None:
        return None
    try:
        return (
            int(off.get("x", "0")),
            int(off.get("y", "0")),
            int(ext.get("cx", "0")),
            int(ext.get("cy", "0")),
        )
    except ValueError:
        return None


def _placeholder_key(shape: ET.Element) -> tuple[str, str] | None:
    holder = shape.find(f"./*/{{{NS_P}}}nvPr/{{{NS_P}}}ph")
    if holder is None:
        return None
    return (holder.get("type") or "body", holder.get("idx") or "")


def _placeholder_boxes(
    zf: zipfile.ZipFile, part: str | None
) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    if part is None or part not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(part))
    except ET.ParseError:
        return {}
    boxes: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for shape in root.iter(f"{{{NS_P}}}sp"):
        key = _placeholder_key(shape)
        box = _xfrm_box(shape.find(f"{{{NS_P}}}spPr/{{{NS_A}}}xfrm"))
        if key is not None and box is not None:
            boxes[key] = box
    return boxes


def _inherited_box(
    key: tuple[str, str],
    layers: list[dict[tuple[str, str], tuple[int, int, int, int]]],
) -> tuple[int, int, int, int] | None:
    kind, index = key
    for boxes in layers:
        if index:
            by_index = [box for (_, idx), box in boxes.items() if idx == index]
            if by_index:
                return by_index[0]
        by_kind = [box for (typed, _), box in boxes.items() if typed == kind]
        if by_kind:
            return by_kind[0]
    return None


Levels = dict[int, float]


def _level_sizes(style: ET.Element | None) -> Levels:
    sizes: Levels = {}
    if style is None:
        return sizes
    for level in range(9):
        properties = style.find(f"{{{NS_A}}}lvl{level + 1}pPr/{{{NS_A}}}defRPr")
        given = properties.get("sz") if properties is not None else None
        if given and given.isdigit():
            sizes[level] = int(given) / 100
    return sizes


def _placeholder_sizes(
    zf: zipfile.ZipFile, part: str | None
) -> dict[tuple[str, str], Levels]:
    if part is None or part not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(part))
    except ET.ParseError:
        return {}
    sizes: dict[tuple[str, str], Levels] = {}
    for shape in root.iter(f"{{{NS_P}}}sp"):
        key = _placeholder_key(shape)
        if key is not None:
            sizes[key] = _level_sizes(
                shape.find(f"{{{NS_P}}}txBody/{{{NS_A}}}lstStyle")
            )
    return sizes


def _placeholder_bodies(
    zf: zipfile.ZipFile, part: str | None
) -> dict[tuple[str, str], ET.Element]:
    if part is None or part not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(part))
    except ET.ParseError:
        return {}
    bodies: dict[tuple[str, str], ET.Element] = {}
    for shape in root.iter(f"{{{NS_P}}}sp"):
        key = _placeholder_key(shape)
        body = shape.find(f"{{{NS_P}}}txBody/{{{NS_A}}}bodyPr")
        if key is not None and body is not None:
            bodies[key] = body
    return bodies


def _master_text_sizes(zf: zipfile.ZipFile, master: str | None) -> dict[str, Levels]:
    if master is None or master not in zf.namelist():
        return {}
    try:
        root = ET.fromstring(zf.read(master))
    except ET.ParseError:
        return {}
    styles = root.find(f"{{{NS_P}}}txStyles")
    if styles is None:
        return {}
    return {
        name: _level_sizes(styles.find(f"{{{NS_P}}}{name}Style"))
        for name in ("title", "body", "other")
    }


@dataclass(frozen=True)
class _TextSizes:
    layers: tuple[dict[tuple[str, str], Levels], ...] = ()
    master: dict[str, Levels] = field(default_factory=dict)
    bodies: tuple[dict[tuple[str, str], ET.Element], ...] = ()

    def body_properties(
        self, shape: ET.Element, key: tuple[str, str] | None
    ) -> list[ET.Element]:
        chain = [shape.find(f"{{{NS_P}}}txBody/{{{NS_A}}}bodyPr")]
        if key is not None:
            kind, index = key
            for bodies in self.bodies:
                matched = [
                    body for (_, idx), body in bodies.items() if index and idx == index
                ] or [body for (typed, _), body in bodies.items() if typed == kind]
                if matched:
                    chain.append(matched[0])
        return [one for one in chain if one is not None]

    def of(self, shape: ET.Element, key: tuple[str, str] | None) -> Levels:
        chain = [_level_sizes(shape.find(f"{{{NS_P}}}txBody/{{{NS_A}}}lstStyle"))]
        if key is not None:
            kind, index = key
            for sizes in self.layers:
                matched = [
                    levels
                    for (_, idx), levels in sizes.items()
                    if index and idx == index
                ] or [levels for (typed, _), levels in sizes.items() if typed == kind]
                if matched:
                    chain.append(matched[0])
            chain.append(
                self.master.get(
                    "title" if kind in ("title", "ctrTitle") else "body", {}
                )
            )
        else:
            chain.append(self.master.get("other", {}))
        resolved: Levels = {}
        for levels in reversed(chain):
            resolved.update(levels)
        return resolved


def _is_bold(run: ET.Element) -> bool:
    properties = run.find(f"{{{NS_A}}}rPr")
    return properties is not None and properties.get("b") in ("1", "true")


def _is_italic(run: ET.Element) -> bool:
    properties = run.find(f"{{{NS_A}}}rPr")
    return properties is not None and properties.get("i") in ("1", "true")


def _paragraphs_of(
    body: ET.Element,
    *,
    title: bool,
    top: float,
    in_table: bool,
    block: int,
    inherited: Levels | None = None,
    scheme: dict[str, str] | None = None,
) -> list[Paragraph]:
    found: list[Paragraph] = []
    for paragraph in body.iter(f"{{{NS_A}}}p"):
        words = 0
        bold_words = 0
        italic_words = 0
        size = 0.0
        drawn = 0.0
        written: list[str] = []
        run_fills: list[tuple[str, str | None]] = []
        properties = paragraph.find(f"{{{NS_A}}}pPr")
        level = properties.get("lvl", "0") if properties is not None else "0"
        margin = properties.get("marL", "0") if properties is not None else "0"
        default = (
            properties.find(f"{{{NS_A}}}defRPr") if properties is not None else None
        )
        stated = default.get("sz") if default is not None else None
        fallback = (
            int(stated) / 100
            if stated and stated.isdigit()
            else (inherited or {}).get(int(level) if level.isdigit() else 0, 0.0)
        )
        for run in paragraph.findall(f"{{{NS_A}}}r"):
            run_text = run.findtext(f"{{{NS_A}}}t") or ""
            written.append(run_text)
            said = len(run_text.split())
            words += said
            bold_words += said if _is_bold(run) else 0
            italic_words += said if _is_italic(run) else 0
            properties = run.find(f"{{{NS_A}}}rPr")
            run_fills.append(
                (
                    run_text,
                    solid_fill_hex(
                        (
                            properties.find(f"{{{NS_A}}}solidFill")
                            if properties is not None
                            else None
                        ),
                        scheme or {},
                    ),
                )
            )
            given = properties.get("sz") if properties is not None else None
            if said and given and given.isdigit():
                size = max(size, int(given) / 100)
            if said:
                drawn = max(
                    drawn,
                    int(given) / 100 if given and given.isdigit() else fallback,
                )
        if words:
            found.append(
                Paragraph(
                    words=words,
                    bold_words=bold_words,
                    size=size,
                    title=title,
                    top=top,
                    in_table=in_table,
                    block=block,
                    level=int(level) if level.isdigit() else 0,
                    margin=int(margin) if margin.isdigit() else 0,
                    text="".join(written).strip(),
                    italic_words=italic_words,
                    inherited_size=fallback,
                    drawn_size=drawn,
                    run_fills=tuple(run_fills),
                )
            )
    return found


_NO_SOLID_FILL = ("noFill", "gradFill", "pattFill", "blipFill")


def shape_fill(
    properties: ET.Element | None,
    style: ET.Element | None,
    scheme: dict[str, str],
    group: str | None = None,
) -> str | None:
    if properties is not None:
        for child in properties:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "solidFill":
                return solid_fill_hex(child, scheme)
            if name == "grpFill":
                return group
            if name in _NO_SOLID_FILL:
                return None
    reference = style.find(f"{{{NS_A}}}fillRef") if style is not None else None
    if reference is None or reference.get("idx", "0") == "0":
        return None
    return solid_fill_hex(reference, scheme)


EMU_PER_POINT = 12700
DEFAULT_POINTS = 18.0
CHARACTER_WIDTH = 0.42
LINE_HEIGHT = 1.2
_INSETS = {"lIns": 91440, "rIns": 91440, "tIns": 45720, "bIns": 45720}


def _first_set(properties: list[ET.Element], name: str) -> str | None:
    return next(
        (value for one in properties if (value := one.get(name)) is not None), None
    )


def _number(element: ET.Element | None, path: str, name: str = "val") -> int | None:
    found = element.find(path) if element is not None else None
    value = found.get(name) if found is not None else None
    return int(value) if value and value.lstrip("-").isdigit() else None


def _text_extent(
    shape: ET.Element,
    body: ET.Element,
    key: tuple[str, str] | None,
    text_sizes: "_TextSizes",
    box: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float, float], ...]:
    left, top, width, height = box
    properties = text_sizes.body_properties(shape, key)
    insets = {
        name: int(given) if (given := _first_set(properties, name)) else default
        for name, default in _INSETS.items()
    }
    scale, reduction = (1.0, 0.0)
    shrinks = False
    for one in properties:
        fitted = one.find(f"{{{NS_A}}}normAutofit")
        if fitted is not None:
            shrinks = True
            scale = int(fitted.get("fontScale", "100000")) / 100000
            reduction = int(fitted.get("lnSpcReduction", "0")) / 100000
            break
    wraps = _first_set(properties, "wrap") != "none"
    levels = text_sizes.of(shape, key)
    usable_width = max(width - insets["lIns"] - insets["rIns"], 1.0)
    usable_height = max(height - insets["tIns"] - insets["bIns"], 1.0)
    drawn_height = 0.0
    spans: list[tuple[float, float, float, float]] = []
    for paragraph in body.findall(f"{{{NS_A}}}p"):
        properties_of = paragraph.find(f"{{{NS_A}}}pPr")
        level = properties_of.get("lvl", "0") if properties_of is not None else "0"
        stated = _number(properties_of, f"{{{NS_A}}}defRPr", "sz")
        default_points = (
            stated / 100
            if stated is not None
            else levels.get(int(level) if level.isdigit() else 0, DEFAULT_POINTS)
        )
        lines_text = [""]
        points = 0.0
        for child in paragraph:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "br":
                lines_text.append("")
            elif tag in ("r", "fld"):
                said = child.findtext(f"{{{NS_A}}}t") or ""
                lines_text[-1] += said
                run = child.find(f"{{{NS_A}}}rPr")
                given = run.get("sz") if run is not None else None
                if said.strip():
                    points = max(
                        points,
                        (
                            int(given) / 100
                            if given and given.isdigit()
                            else default_points
                        ),
                    )
        if not points:
            ending = paragraph.find(f"{{{NS_A}}}endParaRPr")
            given = ending.get("sz") if ending is not None else None
            points = int(given) / 100 if given and given.isdigit() else default_points
        points *= scale
        character = CHARACTER_WIDTH * points * EMU_PER_POINT
        lines = sum(
            max(1, math.ceil(len(one) * character / usable_width)) if wraps else 1
            for one in lines_text
        )
        exact = _number(properties_of, f"{{{NS_A}}}lnSpc/{{{NS_A}}}spcPts")
        line_points = (
            exact / 100
            if exact is not None
            else points
            * LINE_HEIGHT
            * (1 - reduction)
            * (_number(properties_of, f"{{{NS_A}}}lnSpc/{{{NS_A}}}spcPct") or 100000)
            / 100000
        )
        spacing = sum(
            (_number(properties_of, f"{{{NS_A}}}{name}/{{{NS_A}}}spcPts") or 0) / 100
            for name in ("spcBef", "spcAft")
        )
        paragraph_height = (lines * line_points + spacing) * EMU_PER_POINT
        if any(one.strip() for one in lines_text):
            widest = max(len(one) for one in lines_text) * character
            if wraps:
                widest = min(widest, usable_width)
            aligned = (
                properties_of.get("algn", "l") if properties_of is not None else "l"
            )
            inner_left = left + insets["lIns"]
            if aligned == "ctr":
                start = inner_left + (usable_width - widest) / 2
            elif aligned == "r":
                start = inner_left + usable_width - widest
            elif aligned in ("just", "dist") and lines > 1:
                start, widest = (inner_left, usable_width)
            else:
                start = inner_left
            spans.append(
                (start, drawn_height, widest, lines * line_points * EMU_PER_POINT)
            )
        drawn_height += paragraph_height
    if not spans:
        return ()
    if _first_set(properties, "vert") not in (None, "horz"):
        return (box,)
    if usable_width <= 1.0 or usable_height <= 1.0:
        return (box,)
    anchor = _first_set(properties, "anchor") or "t"
    inner_top = top + insets["tIns"]
    if shrinks or anchor != "t":
        drawn_height = min(drawn_height, usable_height)
    if anchor == "ctr":
        text_top = inner_top + (usable_height - drawn_height) / 2
    elif anchor == "b":
        text_top = inner_top + usable_height - drawn_height
    else:
        text_top = inner_top
    bottom = inner_top + max(usable_height, drawn_height)
    placed: list[tuple[float, float, float, float]] = []
    for start, offset, widest, tall in spans:
        line_top = text_top + offset
        if line_top >= bottom:
            break
        placed.append((start, line_top, widest, min(tall, bottom - line_top)))
    return tuple(placed) or ((spans[0][0], text_top, spans[0][2], 0.0),)


def _graphic_kind(frame: ET.Element) -> str:
    data = frame.find(f".//{{{NS_A}}}graphicData")
    uri = data.get("uri", "") if data is not None else ""
    if uri.endswith("/chart") or "chartex" in uri:
        return "chart"
    if uri.endswith("/table"):
        return "table"
    return "shape"


def _drawn_in(
    parent: ET.Element,
    placement: _Placement,
    size: tuple[int, int],
    rels: dict[str, tuple[str, str | None]],
    layers: list[dict[tuple[str, str], tuple[int, int, int, int]]],
    paragraphs: list[Paragraph],
    drawings: list[Drawing],
    text_sizes: _TextSizes | None = None,
    scheme: dict[str, str] | None = None,
    group_fill: str | None = None,
) -> None:
    width, height = size
    palette = scheme or {}
    for child in parent:
        tag = child.tag.rsplit("}", 1)[-1]
        if child.tag == f"{{{NS_MC}}}AlternateContent":
            drawn = len(drawings)
            branches = [
                *child.findall(f"{{{NS_MC}}}Choice"),
                *child.findall(f"{{{NS_MC}}}Fallback"),
            ]
            for branch in branches:
                _drawn_in(
                    branch,
                    placement,
                    size,
                    rels,
                    layers,
                    paragraphs,
                    drawings,
                    text_sizes,
                    scheme,
                    group_fill,
                )
                if len(drawings) > drawn:
                    break
            continue
        if tag == "grpSp":
            group_properties = child.find(f"{{{NS_P}}}grpSpPr")
            xfrm = (
                group_properties.find(f"{{{NS_A}}}xfrm")
                if group_properties is not None
                else None
            )
            placed = _xfrm_box(xfrm)
            inner = _xfrm_box(xfrm, "chOff", "chExt")
            if placed is None or inner is None or inner[2] <= 0 or inner[3] <= 0:
                continue
            x_scale = placement.x_scale * placed[2] / inner[2]
            y_scale = placement.y_scale * placed[3] / inner[3]
            _drawn_in(
                child,
                _Placement(
                    x_scale=x_scale,
                    x_offset=placement.x_offset
                    + placement.x_scale * placed[0]
                    - x_scale * inner[0],
                    y_scale=y_scale,
                    y_offset=placement.y_offset
                    + placement.y_scale * placed[1]
                    - y_scale * inner[1],
                ),
                size,
                rels,
                layers,
                paragraphs,
                drawings,
                text_sizes,
                scheme,
                shape_fill(group_properties, None, palette, group_fill),
            )
            continue
        if tag == "graphicFrame":
            placed = _xfrm_box(child.find(f"{{{NS_P}}}xfrm"))
            kind = _graphic_kind(child)
        elif tag in ("sp", "pic", "cxnSp"):
            properties = child.find(f"{{{NS_P}}}spPr")
            placed = _xfrm_box(
                properties.find(f"{{{NS_A}}}xfrm") if properties is not None else None
            )
            kind = {"sp": "shape", "pic": "picture", "cxnSp": "connector"}[tag]
            if (
                properties is not None
                and properties.find(f"{{{NS_A}}}blipFill") is not None
            ):
                kind = "picture"
        else:
            continue
        key = _placeholder_key(child)
        if placed is None and key is not None:
            placed = _inherited_box(key, layers)
        if placed is None:
            continue
        x, y, w, h = placed
        placed_box = (
            placement.x_offset + placement.x_scale * x,
            placement.y_offset + placement.y_scale * y,
            placement.x_scale * w,
            placement.y_scale * h,
        )
        left, top = (placed_box[0] / width, placed_box[1] / height)
        geometry = child.find(f".//{{{NS_A}}}prstGeom")
        chart_part = ""
        if kind == "chart":
            rid = frame_chart_rid(child)
            target = rels.get(rid) if rid else None
            chart_part = target[0] if target is not None and target[1] is None else ""
        sizes = text_sizes or _TextSizes()
        body = child.find(f"{{{NS_P}}}txBody") if tag == "sp" else None
        found = (
            _paragraphs_of(
                body,
                title=key is not None and key[0] in ("title", "ctrTitle"),
                top=top,
                in_table=False,
                block=len(drawings) + 1,
                inherited=sizes.of(child, key),
                scheme=palette,
            )
            if body is not None
            else []
        )
        lines = (
            _text_extent(child, body, key, sizes, placed_box)
            if body is not None
            else ()
        )
        if kind == "table":
            rows = sum(
                int(row.get("h", "0"))
                for row in child.iter(f"{{{NS_A}}}tr")
                if row.get("h", "").isdigit()
            )
            if rows:
                lines = (
                    (
                        placed_box[0],
                        placed_box[1],
                        placed_box[2],
                        placement.y_scale * rows,
                    ),
                )
        turned = (
            child.find(f"{{{NS_P}}}xfrm")
            if tag == "graphicFrame"
            else child.find(f"{{{NS_P}}}spPr/{{{NS_A}}}xfrm")
        )
        rotation = turned.get("rot", "0") if turned is not None else "0"
        named = child.find(f"./*/{{{NS_P}}}cNvPr")
        drawings.append(
            Drawing(
                kind=kind,
                geometry=(geometry.get("prst") or "") if geometry is not None else "",
                texted=kind == "shape"
                and any((t.text or "").strip() for t in child.iter(f"{{{NS_A}}}t")),
                x=left,
                y=top,
                w=placed_box[2] / width,
                h=placed_box[3] / height,
                chart_part=chart_part,
                fill=(
                    shape_fill(
                        child.find(f"{{{NS_P}}}spPr"),
                        child.find(f"{{{NS_P}}}style"),
                        palette,
                        group_fill,
                    )
                    if kind == "shape" and tag == "sp"
                    else None
                ),
                element=tag,
                name=named.get("name", "") if named is not None else "",
                placeholder=key[0] if key is not None else "",
                rotation=(
                    int(rotation) / 60000 if rotation.lstrip("-").isdigit() else 0.0
                ),
                words=sum(one.words for one in found),
                lines=tuple(
                    (
                        one[0] / width,
                        one[1] / height,
                        one[2] / width,
                        one[3] / height,
                    )
                    for one in lines
                ),
            )
        )
        paragraphs += found
        if kind == "table":
            for cell in child.iter(f"{{{NS_A}}}tc"):
                body = cell.find(f"{{{NS_A}}}txBody")
                if body is not None:
                    paragraphs += _paragraphs_of(
                        body,
                        title=False,
                        top=top,
                        in_table=True,
                        block=len(drawings),
                        scheme=palette,
                    )


def _layout_type(zf: zipfile.ZipFile, layout: str | None) -> str:
    if not layout or layout not in zf.namelist():
        return ""
    try:
        return ET.fromstring(zf.read(layout)).get("type", "")
    except ET.ParseError:
        return ""


def slide_drawings(raw: bytes) -> list[SlideDrawing]:
    zf = open_deck(raw)
    if zf is None:
        return []
    with zf:
        size = presentation_slide_size(raw) or DEFAULT_SLIDE_SIZE_EMU
        deck_wide = theme_colors(raw)
        slides: list[SlideDrawing] = []
        for number, name in enumerate(ordered_slide_names(zf), start=1):
            try:
                root = ET.fromstring(zf.read(name))
            except ET.ParseError:
                slides.append(SlideDrawing(number=number, paragraphs=(), drawings=()))
                continue
            layout = first_rel_target(zf, name, "slideLayout")
            master = first_rel_target(zf, layout, "slideMaster") if layout else None
            paragraphs: list[Paragraph] = []
            drawings: list[Drawing] = []
            tree = root.find(f".//{{{NS_P}}}spTree")
            if tree is not None:
                _drawn_in(
                    tree,
                    _Placement(),
                    size,
                    part_rels(zf, name),
                    [_placeholder_boxes(zf, layout), _placeholder_boxes(zf, master)],
                    paragraphs,
                    drawings,
                    _TextSizes(
                        layers=(
                            _placeholder_sizes(zf, layout),
                            _placeholder_sizes(zf, master),
                        ),
                        master=_master_text_sizes(zf, master),
                        bodies=(
                            _placeholder_bodies(zf, layout),
                            _placeholder_bodies(zf, master),
                        ),
                    ),
                    slide_scheme(zf, name, deck_wide),
                )
            slides.append(
                SlideDrawing(
                    number=number,
                    paragraphs=tuple(paragraphs),
                    drawings=tuple(drawings),
                    hidden=root.get("show") in ("0", "false"),
                    layout_type=_layout_type(zf, layout),
                )
            )
        return slides
