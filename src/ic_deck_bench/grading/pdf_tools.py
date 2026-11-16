import asyncio
import functools
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ic_deck_bench.core import Unreachable

logger = logging.getLogger(__name__)

PDFTOPPM = "pdftoppm"
PDFINFO = "pdfinfo"
PDFTOTEXT = "pdftotext"
PDFIMAGES = "pdfimages"
POPPLER_INSTALL = "apt install poppler-utils"
TIMEOUT_SECONDS = 300
POINTS_PER_INCH = 72
_CONCURRENT_CALLS = 8
_PAGE_FILE = re.compile(r"-(\d+)\.(?:png|jpg)$")

_call_slots: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


@dataclass(frozen=True)
class Scale:
    factor: float


@dataclass(frozen=True)
class Width:
    pixels: int


PageSize = Scale | Width


@functools.cache
def _missing_on(path: str | None) -> tuple[str, ...]:
    return tuple(
        tool
        for tool in (PDFTOPPM, PDFINFO, PDFTOTEXT, PDFIMAGES)
        if shutil.which(tool, path=path) is None
    )


def require_poppler() -> None:
    missing = _missing_on(os.environ.get("PATH"))
    if missing:
        raise Unreachable(
            f"{', '.join(f'`{tool}`' for tool in missing)} not on PATH; install "
            f"Poppler with `{POPPLER_INSTALL}`"
        )


@contextmanager
def _scratch(data: bytes) -> Iterator[tuple[Path, Path]]:
    require_poppler()
    with tempfile.TemporaryDirectory(prefix="pdf-tools-") as raw:
        scratch = Path(raw)
        pdf = scratch / "in.pdf"
        pdf.write_bytes(data)
        yield pdf, scratch


def _image_argv(
    pdf: Path,
    scratch: Path,
    size: PageSize,
    *,
    page: int | None = None,
    jpeg_quality: int | None = None,
) -> list[str]:
    argv = [PDFTOPPM, "-cropbox"]
    argv += (
        ["-png"]
        if jpeg_quality is None
        else ["-jpeg", "-jpegopt", f"quality={jpeg_quality}"]
    )
    if isinstance(size, Scale):
        argv += ["-r", repr(POINTS_PER_INCH * size.factor)]
    else:
        argv += ["-scale-to-x", str(size.pixels), "-scale-to-y", "-1"]
    if page is not None:
        argv += ["-f", str(page + 1), "-l", str(page + 1)]
    return [*argv, str(pdf), str(scratch / "page")]


def _images_in(scratch: Path) -> list[bytes]:
    numbered = [
        (int(found.group(1)), path)
        for path in scratch.iterdir()
        if (found := _PAGE_FILE.search(path.name)) is not None
    ]
    return [path.read_bytes() for (_, path) in sorted(numbered)]


def _texts_in(stdout: bytes) -> list[str]:
    pages = stdout.decode("utf-8", "replace").split("\f")
    return pages[:-1] if pages and pages[-1] == "" else pages


def _hung(argv: list[str]) -> None:
    logger.warning(
        {"type": "poppler_hung", "tool": argv[0], "seconds": TIMEOUT_SECONDS}
    )


def _ran(argv: list[str]) -> bytes | None:
    try:
        done = subprocess.run(
            argv, capture_output=True, timeout=TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired:
        _hung(argv)
        return None
    return done.stdout if done.returncode == 0 else None


async def _ran_async(argv: list[str]) -> bytes | None:
    async with _call_slots.setdefault(
        asyncio.get_running_loop(), asyncio.Semaphore(_CONCURRENT_CALLS)
    ):
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
        except TimeoutError:
            process.kill()
            await process.wait()
            _hung(argv)
            return None
    return stdout if process.returncode == 0 else None


async def page_images_async(data: bytes, size: PageSize) -> list[bytes] | None:
    with _scratch(data) as (pdf, scratch):
        if await _ran_async(_image_argv(pdf, scratch, size)) is None:
            return None
        return _images_in(scratch)


def page_texts(data: bytes, *, first_pages: int | None = None) -> list[str] | None:
    pages = [] if first_pages is None else ["-f", "1", "-l", str(first_pages)]
    with _scratch(data) as (pdf, _):
        stdout = _ran([PDFTOTEXT, "-raw", *pages, "-enc", "UTF-8", str(pdf), "-"])
    return None if stdout is None else _texts_in(stdout)
