from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DocxLevel:
    num_fmt: str | None = None
    lvl_text: str | None = None
    font: str | None = None


@dataclass(frozen=True, slots=True)
class DocxRun:
    text: str
    font: str | None = None
    size_pt: float | None = None
    bold: bool = False
    color: str | None = None
    highlighted: bool = False
    superscript: bool = False


@dataclass(frozen=True, slots=True)
class DocxParagraph:
    text: str
    style: str | None = None
    num_id: str | None = None
    ilvl: int | None = None
    num_fmt: str | None = None
    lvl_text: str | None = None
    marker_font: str | None = None
    runs: tuple[DocxRun, ...] = ()
    in_table: bool = False
    table_index: int | None = None
    shading: str | None = None
    alignment: str | None = None
    image_count: int = 0
    image_width_twips: int = 0
    footnotes: tuple[str, ...] = ()
    text_width_twips: int | None = None

    @property
    def is_listed(self) -> bool:
        return self.num_id is not None

    @property
    def is_bullet(self) -> bool:
        return self.num_fmt == "bullet"

    @property
    def is_bullet_like(self) -> bool:
        style = (self.style or "").lower()
        if "bullet" in style or "list" in style or self.is_listed:
            return True
        return self.text.strip()[:1] in ("•", "▪", "‣", "◦", "●")


@dataclass(frozen=True, slots=True)
class DocxCell:
    text: str
    shading: str | None = None
    image_count: int = 0
    tables: tuple["DocxTable", ...] = ()
    visible_text: str = ""


@dataclass(frozen=True, slots=True)
class DocxTable:
    rows: int
    columns: int
    cells: tuple[tuple[str, ...], ...] = ()
    grid: tuple[tuple[DocxCell, ...], ...] = ()
    width_twips: int | None = None
    text_width_twips: int | None = None

    @property
    def is_empty(self) -> bool:
        return not any(cell.strip() for row in self.cells for cell in row)

    @property
    def nested(self) -> list["DocxTable"]:
        found: list["DocxTable"] = [self]
        for row in self.grid:
            for cell in row:
                for table in cell.tables:
                    found.extend(table.nested)
        return found


@dataclass(frozen=True, slots=True)
class DocxFooter:
    part: str
    text: str = ""
    has_page_field: bool = False


@dataclass(frozen=True, slots=True)
class DocxMargins:
    top: int | None = None
    bottom: int | None = None
    left: int | None = None
    right: int | None = None


@dataclass(frozen=True, slots=True)
class DocxPage:
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class DocxDocument:
    paragraphs: tuple[DocxParagraph, ...] = ()
    tables: tuple[DocxTable, ...] = ()
    footers: tuple[DocxFooter, ...] = ()
    margins: tuple[DocxMargins, ...] = ()
    pages: tuple[DocxPage, ...] = ()
    footnotes: tuple[str, ...] = ()
    footnote_references: int = 0
    body_image_count: int = 0
    header_image_count: int = 0
    default_font: str | None = None
    default_size_pt: float | None = None
    levels: dict[tuple[str, int], DocxLevel] = field(default_factory=dict)
    text_width_twips: int | None = None

    @property
    def body_paragraphs(self) -> list[DocxParagraph]:
        return [para for para in self.paragraphs if not para.in_table]

    @property
    def style_names(self) -> list[str | None]:
        return [para.style for para in self.paragraphs if para.text.strip()]

    @property
    def all_tables(self) -> list[DocxTable]:
        return [one for table in self.tables for one in table.nested]

    def paragraph_with(self, substring: str) -> DocxParagraph | None:
        return next(iter(self.paragraphs_with(substring)), None)

    def paragraphs_with(self, substring: str) -> list[DocxParagraph]:
        needle = substring.lower()
        return [para for para in self.paragraphs if needle in para.text.lower()]

    def body_paragraphs_with(self, substring: str) -> list[DocxParagraph]:
        needle = substring.lower()
        return [para for para in self.body_paragraphs if needle in para.text.lower()]

    @property
    def footer_with_page_field(self) -> DocxFooter | None:
        return next((one for one in self.footers if one.has_page_field), None)
