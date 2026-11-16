from ic_deck_bench.grading import pdf_tools


def page_texts(data: bytes) -> list[str] | None:
    return pdf_tools.page_texts(data)
