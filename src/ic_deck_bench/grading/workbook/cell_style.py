import string


def rgb_channels(rgb: str) -> tuple[int, int, int]:
    raw = rgb[2:] if len(rgb) == 8 else rgb
    return int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16)


def hex_to_rgb(hex_text: str | None) -> tuple[int, int, int] | None:
    if hex_text is None:
        return None
    raw = hex_text.strip().lstrip("#")
    if len(raw) not in (6, 8) or not all(c in string.hexdigits for c in raw):
        return None
    return rgb_channels(raw)
