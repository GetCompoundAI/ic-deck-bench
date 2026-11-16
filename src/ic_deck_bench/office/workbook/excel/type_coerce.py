from datetime import datetime

from ic_deck_bench.office.workbook.types import ExcelErrorType


def datetime_to_serial_number(value: datetime) -> float | ExcelErrorType:
    days = (value - datetime(1900, 1, 1)).total_seconds() / 86400.0

    serial = days + 1

    if value >= datetime(1900, 3, 1):
        serial += 1

    if serial < -1:
        return ExcelErrorType.VALUE

    return serial
