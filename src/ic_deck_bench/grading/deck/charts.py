import xml.etree.ElementTree as ET
from dataclasses import dataclass

from ic_deck_bench.office.deliverable_checks.deck import NS_A, NS_C, NS_CX


@dataclass(frozen=True)
class ChartSeries:
    plot: str
    name: str
    categories: tuple[str, ...]
    values: tuple[float | None, ...]
    x_values: tuple[float | None, ...] = ()
    secondary: bool = False
    unfilled: bool = False
    background: bool = False
    grouping: str = ""
    overlap: int = 0
    direction: str = ""
    subtotals: frozenset[int] = frozenset()
    highlighted: frozenset[int] = frozenset()


def _c(tag: str) -> str:
    return f"{{{NS_C}}}{tag}"


def _cx(tag: str) -> str:
    return f"{{{NS_CX}}}{tag}"


def _number(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except ValueError:
        return None


def _points(cache: ET.Element | None, tag: str) -> dict[int, str]:
    if cache is None:
        return {}
    found: dict[int, str] = {}
    for point in cache.iter(tag):
        index = point.get("idx")
        value = point.find(f"{{{NS_C}}}v") if tag == _c("pt") else point
        if index is not None and index.isdigit() and value is not None:
            found[int(index)] = value.text or ""
    return found


def _count(cache: ET.Element | None, found: dict[int, str]) -> int:
    count = None if cache is None else cache.find(f".//{_c('ptCount')}")
    declared = count.get("val") if count is not None else None
    return max(
        int(declared) if declared and declared.isdigit() else 0,
        max(found, default=-1) + 1,
    )


def _numbers(element: ET.Element | None) -> tuple[float | None, ...]:
    if element is None:
        return ()
    found = _points(element, _c("pt"))
    return tuple(_number(found.get(index)) for index in range(_count(element, found)))


def _labels(element: ET.Element | None) -> tuple[str, ...]:
    if element is None:
        return ()
    level = element.find(f".//{_c('lvl')}")
    holder = level if level is not None else element
    found = _points(holder, _c("pt"))
    return tuple(found.get(index, "") for index in range(_count(element, found)))


def _series_name(series: ET.Element) -> str:
    return " ".join(
        value.text or "" for value in series.findall(f"{_c('tx')}//{_c('v')}")
    ).strip()


def _unfilled(properties: ET.Element | None) -> bool:
    if properties is None:
        return False
    if properties.find(f"{{{NS_A}}}noFill") is not None:
        return True
    solid = properties.find(f"{{{NS_A}}}solidFill")
    return solid is not None and any(
        alpha.get("val") == "0" for alpha in solid.iter(f"{{{NS_A}}}alpha")
    )


def _background(properties: ET.Element | None) -> bool:
    if properties is None:
        return False
    solid = properties.find(f"{{{NS_A}}}solidFill")
    if solid is None:
        return False
    scheme = solid.find(f"{{{NS_A}}}schemeClr")
    rgb = solid.find(f"{{{NS_A}}}srgbClr")
    return (scheme is not None and scheme.get("val") in ("bg1", "lt1")) or (
        rgb is not None and (rgb.get("val") or "").upper() == "FFFFFF"
    )


def _overlap(plot: ET.Element) -> int:
    element = plot.find(_c("overlap"))
    value = None if element is None else _number(element.get("val"))
    return 0 if value is None else int(value)


def _value_axis(plot: ET.Element) -> str | None:
    ids = [one.get("val") for one in plot.findall(_c("axId"))]
    return ids[1] if len(ids) > 1 else None


def _chart_parts(root: ET.Element) -> list[ChartSeries]:
    plot_area = root.find(f"{_c('chart')}/{_c('plotArea')}")
    if plot_area is None:
        return []
    plots = [one for one in plot_area if one.tag.endswith("Chart")]
    crossing_at_max = {
        identity.get("val")
        for axis in plot_area.findall(_c("valAx"))
        if (identity := axis.find(_c("axId"))) is not None
        and (crosses := axis.find(_c("crosses"))) is not None
        and crosses.get("val") == "max"
    }
    on_primary = [one for one in plots if _value_axis(one) not in crossing_at_max]
    primary = _value_axis((on_primary or plots)[0]) if plots else None
    found: list[ChartSeries] = []
    for plot in plots:
        tag = plot.tag.rsplit("}", 1)[-1]
        grouping = plot.find(_c("grouping"))
        direction = plot.find(_c("barDir"))
        secondary = _value_axis(plot) != primary
        for series in plot.findall(_c("ser")):
            scatter = tag in ("scatterChart", "bubbleChart")
            categories = series.find(_c("xVal" if scatter else "cat"))
            found.append(
                ChartSeries(
                    plot=tag,
                    name=_series_name(series),
                    categories=() if scatter else _labels(categories),
                    values=_numbers(series.find(_c("yVal" if scatter else "val"))),
                    x_values=_numbers(categories) if scatter else (),
                    secondary=secondary,
                    unfilled=_unfilled(series.find(_c("spPr"))),
                    background=_background(series.find(_c("spPr"))),
                    grouping="" if grouping is None else grouping.get("val", ""),
                    overlap=_overlap(plot),
                    direction="" if direction is None else direction.get("val", ""),
                    highlighted=frozenset(
                        int(index.get("val", "-1"))
                        for point in series.findall(_c("dPt"))
                        if (index := point.find(_c("idx"))) is not None
                        and point.find(f"{_c('spPr')}/{{{NS_A}}}solidFill") is not None
                    ),
                )
            )
    return found


def _chart_ex_parts(root: ET.Element) -> list[ChartSeries]:
    data = {one.get("id"): one for one in root.iter(_cx("data"))}
    found: list[ChartSeries] = []
    for series in root.iter(_cx("series")):
        data_id = series.find(_cx("dataId"))
        block = data.get(None if data_id is None else data_id.get("val"))
        categories: tuple[str, ...] = ()
        values: tuple[float | None, ...] = ()
        if block is not None:
            labels = block.find(_cx("strDim"))
            numbers = block.find(_cx("numDim"))
            if labels is not None:
                points = {
                    int(one.get("idx", "-1")): one.text or ""
                    for one in labels.iter(_cx("pt"))
                }
                categories = tuple(
                    points.get(index, "")
                    for index in range(max(points, default=-1) + 1)
                )
            if numbers is not None:
                points = {
                    int(one.get("idx", "-1")): one.text or ""
                    for one in numbers.iter(_cx("pt"))
                }
                values = tuple(
                    _number(points.get(index))
                    for index in range(max(points, default=-1) + 1)
                )
        name = " ".join(
            one.text or "" for one in series.findall(f"{_cx('tx')}//{_cx('v')}")
        ).strip()
        found.append(
            ChartSeries(
                plot=series.get("layoutId", ""),
                name=name,
                categories=categories,
                values=values,
                unfilled=_unfilled(series.find(_cx("spPr"))),
                subtotals=frozenset(
                    int(one.get("val", "-1"))
                    for one in series.iter(_cx("idx"))
                    if one.get("val", "").isdigit()
                ),
            )
        )
    return found


def chart_series(chart_xml: bytes) -> list[ChartSeries]:
    try:
        root = ET.fromstring(chart_xml)
    except ET.ParseError:
        return []
    if root.tag.startswith(f"{{{NS_CX}}}"):
        return _chart_ex_parts(root)
    return _chart_parts(root)


@dataclass(frozen=True)
class Bar:
    label: str
    bottom: float
    top: float


def _stacked_bars(series: list[ChartSeries]) -> list[ChartSeries]:
    return [
        one
        for one in series
        if one.plot in ("barChart", "bar3DChart") and one.grouping == "stacked"
    ]


def _beneath(stacked: list[ChartSeries]) -> list[ChartSeries]:
    first = next(
        (index for index, one in enumerate(stacked) if not one.unfilled), len(stacked)
    )
    return [one for one in stacked[:first] if one.unfilled]


def _overlaid_bars(
    series: list[ChartSeries],
) -> tuple[list[ChartSeries], list[ChartSeries]]:
    overlaid = [
        one
        for one in series
        if one.plot == "barChart"
        and one.grouping in ("", "clustered", "standard")
        and one.overlap >= 100
    ]
    first = next(
        (
            index
            for index, one in enumerate(overlaid)
            if not one.unfilled and not one.background
        ),
        None,
    )
    if first is None:
        return ([], [])
    masks = [one for one in overlaid[first + 1 :] if one.background]
    if not masks:
        return ([], [])
    last = overlaid.index(masks[0])
    filled = [
        one for one in overlaid[first:last] if not one.unfilled and not one.background
    ]
    return (filled, masks)


def floats(series: list[ChartSeries]) -> bool:
    stacked = _stacked_bars(series)
    if any(value for one in _beneath(stacked) for value in one.values if value) and any(
        not one.unfilled for one in stacked
    ):
        return True
    filled, masks = _overlaid_bars(series)
    return bool(filled) and any(value for one in masks for value in one.values if value)


def _overlaid_floating_bars(
    filled: list[ChartSeries], masks: list[ChartSeries]
) -> list[Bar]:
    labels = max((one.categories for one in filled), key=len)
    count = max(len(one.values) for one in filled)
    bars: list[Bar] = []
    for index in range(count):
        top = max(
            (one.values[index] or 0.0 for one in filled if index < len(one.values)),
            default=0.0,
        )
        bottom = max(
            (one.values[index] or 0.0 for one in masks if index < len(one.values)),
            default=0.0,
        )
        label = labels[index] if index < len(labels) else ""
        bars.append(Bar(label=label, bottom=min(bottom, top), top=top))
    return bars


def floating_bars(series: list[ChartSeries]) -> list[Bar]:
    stacked = _stacked_bars(series)
    if not stacked:
        filled, masks = _overlaid_bars(series)
        return _overlaid_floating_bars(filled, masks) if masks else []
    beneath = _beneath(stacked)
    labels = max((one.categories for one in stacked), key=len)
    count = max(len(one.values) for one in stacked)
    bars: list[Bar] = []
    for index in range(count):
        base = sum(
            one.values[index] or 0.0 for one in beneath if index < len(one.values)
        )
        height = sum(
            abs(one.values[index] or 0.0)
            for one in stacked
            if not one.unfilled and index < len(one.values)
        )
        label = labels[index] if index < len(labels) else ""
        bars.append(Bar(label=label, bottom=base, top=base + height))
    return bars
