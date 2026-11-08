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

from django.http import Http404, HttpResponse, StreamingHttpResponse

from .services import CONTENT_TYPES

CHUNK_BYTES = 64 * 1024
RANGE_HEADER = re.compile(r"^bytes=(\d*)-(\d*)$")


def _read(handle, start, length):
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


def ranged_clip_response(request, short):
    storage, name = short.clip.storage, short.clip.name
    try:
        size = storage.size(name)
        handle = storage.open(name, "rb")
    except (FileNotFoundError, OSError):
        raise Http404("Clip file unavailable.")

    start, end, status = 0, size - 1, 200
    # A header this does not understand (e.g. several ranges at once) is
    # ignored and the whole file is sent, which the HTTP spec allows.
    match = RANGE_HEADER.match(request.headers.get("Range", "").strip())
    if match and (match[1] or match[2]):
        if match[1]:
            start = int(match[1])
            end = min(int(match[2]), size - 1) if match[2] else size - 1
        else:  # "bytes=-500" means the last 500 bytes
            start = max(size - int(match[2]), 0)
        if start >= size or start > end:
            handle.close()
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        status = 206

    length = end - start + 1
    extension = os.path.splitext(name)[1].lower()
    response = StreamingHttpResponse(
        _read(handle, start, length),
        status=status,
        content_type=CONTENT_TYPES.get(extension, "video/mp4"),
    )
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = "private, max-age=3600"
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response
