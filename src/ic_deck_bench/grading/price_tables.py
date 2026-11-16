import re
from dataclasses import dataclass

from ic_deck_bench.grading.doc.docx import DocxTable

_AMOUNT = re.compile(
    r"\(?\s*(?P<sign>[-–−])?\s*(?:US)?\$?\s*"
    r"(?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mm|m|million|bn|billion|k|thousand)?\s*\)?",
    re.IGNORECASE,
)
UNIT_IN_MILLIONS = {
    "k": 1e-3,
    "thousand": 1e-3,
    "m": 1.0,
    "mm": 1.0,
    "million": 1.0,
    "bn": 1e3,
    "billion": 1e3,
}
TOTAL_LABEL = re.compile(r"\b(?:sub-?)?totals?\b", re.IGNORECASE)
FIGURE_IN_TEXT = re.compile(
    r"(?<![\w.])\$?\s*(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(mm|m|million|k|thousand|bn|billion)?\b",
    re.IGNORECASE,
)


def table_lines(table: DocxTable, notes: dict[str, str] | None = None) -> list[str]:
    notes = notes or {}
    lines = []
    for row in table.cells:
        line = " | ".join(cell.strip() for cell in row)
        cited = [notes[cell.strip()] for cell in row if cell.strip() in notes]
        lines.append(" ".join([line, *dict.fromkeys(cited)]))
    return lines


def _amount(cell: str) -> float | None:
    text = cell.strip()
    found = _AMOUNT.fullmatch(text)
    if found is None:
        return None
    value = float(found["number"].replace(",", ""))
    unit = (found["unit"] or "").lower()
    value *= UNIT_IN_MILLIONS.get(unit, 1.0)
    negative = bool(found["sign"]) or (text.startswith("(") and text.endswith(")"))
    return -value if negative else value


_ROW_NUMBER = re.compile(r"\s*[A-Z]?\d{1,2}(?:\.\d{1,2})?[.)]?\s*")


def _amounts(row: tuple[str, ...]) -> tuple[float, ...]:
    cells = row[1:] if row and _ROW_NUMBER.fullmatch(row[0]) else row
    return tuple(number for cell in cells if (number := _amount(cell)) is not None)


@dataclass(frozen=True)
class Row:
    label: str
    values: tuple[float, ...]
    price: float | None


@dataclass(frozen=True)
class PriceTable:
    items: tuple[Row, ...]
    total: float
    foots: bool
    opening: float | None = None
    closing: float | None = None


def _label(row: tuple[str, ...]) -> str:
    return " ".join(
        cell.strip() for cell in row if cell.strip() and _amount(cell) is None
    )


_LARGEST_PRICE_TOTAL = 2_000.0


def _tolerance(items: int) -> float:
    return 0.05 * items + 0.05


def _column_entries(
    rows: list[tuple[str, ...]], back: int
) -> list[tuple[tuple[str, ...], float, bool]]:
    return [
        (row, value, bool(TOTAL_LABEL.search(_label(row))))
        for row in rows
        if len(row) >= back
        and not (len(row) == back and _ROW_NUMBER.fullmatch(row[0]))
        and (value := _amount(row[-back])) is not None
    ]


def _items_of(
    body: list[tuple[tuple[str, ...], float, bool]], implicit: bool
) -> list[tuple[tuple[str, ...], float]]:
    items: list[tuple[tuple[str, ...], float]] = []
    run: list[float] = []
    for row, value, labelled in body:
        if labelled:
            run = []
            continue
        if implicit and len(run) >= 2 and abs(sum(run) - value) <= _tolerance(len(run)):
            run = []
            continue
        items.append((row, value))
        run.append(value)
    return items


def column_tables(rows: list[tuple[str, ...]], back: int) -> list[PriceTable]:
    entries = _column_entries(rows, back)
    labelled = [value for _row, value, is_total in entries if is_total]
    stated = max(labelled, key=abs) if labelled else None
    found: list[PriceTable] = []
    for implicit in (False, True):
        for opening in (False, True):
            for closing in (False, True):
                body = list(entries)
                first = last = None
                if opening:
                    if not body or body[0][2]:
                        continue
                    first = body.pop(0)[1]
                if closing:
                    if not body or body[-1][2]:
                        continue
                    last = body.pop()[1]
                items = _items_of(body, implicit)
                if len(items) < 3:
                    continue
                if first is not None and last is not None:
                    bridged = last - first
                    if stated is not None and abs(abs(stated) - abs(bridged)) > (
                        _tolerance(len(items))
                    ):
                        continue
                    total = stated if stated is not None else bridged
                elif stated is not None:
                    total = stated
                else:
                    continue
                if abs(total) > _LARGEST_PRICE_TOTAL:
                    continue
                summed = sum(value for _row, value in items)
                if abs(abs(summed) - abs(total)) > _tolerance(len(items)):
                    continue
                found.append(
                    _priced(items, total, foots=True, opening=first, closing=last)
                )
                return found
    if stated is not None and abs(stated) <= _LARGEST_PRICE_TOTAL:
        items = _items_of(entries, implicit=False)
        if len(items) >= 3:
            found.append(_priced(items, stated, foots=False))
    return found


def _priced(
    items: list[tuple[tuple[str, ...], float]],
    total: float,
    *,
    foots: bool,
    opening: float | None = None,
    closing: float | None = None,
) -> PriceTable:
    return PriceTable(
        items=tuple(
            Row(label=_label(row), values=_amounts(row), price=value)
            for row, value in items
        ),
        total=total,
        foots=foots,
        opening=opening,
        closing=closing,
    )


def stated_total(table: DocxTable) -> list[PriceTable]:
    rows = [tuple(row) for row in table.cells]
    widest = max((len(row) for row in rows), default=0)
    columns = [
        one for back in range(1, widest + 1) for one in column_tables(rows, back)
    ]
    return sorted(columns, key=lambda one: abs(one.total), reverse=True)[:1]


def amounts_beside_a_total(table: DocxTable) -> bool:
    return any(
        TOTAL_LABEL.search(_label(tuple(row))) and _amounts(tuple(row))
        for row in table.cells
    )
