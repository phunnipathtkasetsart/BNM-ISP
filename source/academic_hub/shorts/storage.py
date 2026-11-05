"""Where Shorts clips are kept.

The clip field asks for its storage through this function rather than naming
a backend, so the choice stays in settings.STORAGES["shorts"]: MinIO under
docker-compose, the local disk anywhere else. Migrations record only this
function's path, so switching backends never creates a migration.
"""

from django.core.files.storage import storages


def shorts_storage():
    return storages["shorts"]
