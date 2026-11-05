"""Where Shorts clips are kept, and the links they are played from.

The clip field asks for its storage through shorts_storage() rather than
naming a backend, so the choice stays in settings.STORAGES["shorts"]: MinIO
under docker-compose, the local disk anywhere else. Migrations record only
this function's path, so switching backends never creates a migration.
"""

from functools import lru_cache

from django.conf import settings
from django.core.files.storage import storages
from django.http.request import split_domain_port


def shorts_storage():
    return storages["shorts"]


def public_minio_url(request):
    """The MinIO address as the viewer's browser sees it.

    Inside Docker the app talks to http://minio:9000, a name only the
    containers know. A browser needs the address of the machine instead, which
    is the host it opened the site on: localhost on the laptop, or the
    laptop's Wi-Fi address on a phone. MINIO_PUBLIC_URL overrides this.
    """
    if settings.MINIO_PUBLIC_URL:
        return settings.MINIO_PUBLIC_URL
    host, _port = split_domain_port(request.get_host())
    return f"{request.scheme}://{host}:{settings.MINIO_PUBLIC_PORT}"


@lru_cache(maxsize=8)
def _signing_client(endpoint_url):
    """A boto3 client used only to sign links; signing makes no network call.

    It has to be separate from the storage's own client because a signed link
    is only valid for the host it was signed for.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def signed_clip_url(short, request):
    """A time-limited link to the clip in MinIO's private bucket."""
    client = _signing_client(public_minio_url(request))
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.SHORTS_BUCKET, "Key": short.clip.name},
        ExpiresIn=settings.SHORTS_URL_SECONDS,
    )
