"""Serve a clip from local storage with HTTP Range support.

A video player asks for the file one piece at a time ("Range: bytes=...")
to start quickly and to seek, and Safari on iPhone will not play a video at
all from a server that ignores those requests. Django's FileResponse always
sends the whole file, so this answers ranges itself.

Only used when SHORTS_STORAGE is "local". With MinIO the browser is sent
straight to MinIO, which handles ranges natively.
"""

import os
import re
from collections.abc import Iterator
from typing import IO

from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    StreamingHttpResponse,
)
from django.http.response import HttpResponseBase

from .models import Short
from .services import content_type_for

CHUNK_BYTES = 64 * 1024
RANGE_HEADER = re.compile(r"^bytes=(\d*)-(\d*)$")


class UnsatisfiableRange(ValueError):
    """The requested range starts past the end of the file."""


def _read(handle: IO[bytes], start: int, length: int) -> Iterator[bytes]:
    try:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            data = handle.read(min(CHUNK_BYTES, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data
    finally:
        handle.close()


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """The (start, end) bytes a Range header asks for, inclusive.

    None means "send the whole file": no header, or one this does not
    understand (e.g. several ranges at once), which the HTTP spec allows.
    Raises UnsatisfiableRange when the range is outside the file.
    """
    match = RANGE_HEADER.match(header.strip())
    if not match or not (match[1] or match[2]):
        return None
    if match[1]:
        start = int(match[1])
        end = min(int(match[2]), size - 1) if match[2] else size - 1
    else:  # "bytes=-500" means the last 500 bytes
        start, end = max(size - int(match[2]), 0), size - 1
    if start >= size or start > end:
        raise UnsatisfiableRange
    return start, end


def ranged_clip_response(
    request: HttpRequest, short: Short
) -> HttpResponseBase:
    """The clip's bytes, or the part of them the Range header asks for."""
    storage, name = short.clip.storage, short.clip.name
    try:
        size = storage.size(name)
        handle = storage.open(name, "rb")
    except OSError as error:  # FileNotFoundError is an OSError
        raise Http404("Clip file unavailable.") from error

    try:
        wanted = parse_range(request.headers.get("Range", ""), size)
    except UnsatisfiableRange:
        handle.close()
        refused = HttpResponse(status=416)
        refused["Content-Range"] = f"bytes */{size}"
        return refused

    start, end = wanted or (0, size - 1)
    response = StreamingHttpResponse(
        _read(handle, start, end - start + 1),
        status=206 if wanted else 200,
        content_type=content_type_for(os.path.basename(name)),
    )
    response["Content-Length"] = str(end - start + 1)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=3600"
    if wanted:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response
