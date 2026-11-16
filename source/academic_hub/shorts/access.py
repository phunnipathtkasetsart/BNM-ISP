"""Who is signed in, and who may post and manage shorts."""

from django.contrib.auth.decorators import login_required
from django.db.models import QuerySet
from django.http import HttpRequest

from accounts.models import User as Account

from .models import Short

# Every page needs a signed-in user; anyone else goes to the sign-in page.
signed_in_page = login_required(login_url="accounts:login")


def account_of(request: HttpRequest) -> Account | None:
    """The signed-in account, or None for an anonymous visitor."""
    user = request.user
    return user if isinstance(user, Account) else None


def can_create_shorts(user: object) -> bool:
    """Lecturers and the Department may post shorts. Students may not."""
    return (
        isinstance(user, Account)
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


def manageable_shorts(user: Account) -> "QuerySet[Short]":
    """The shorts this user may edit and delete.

    Lecturers manage the shorts they uploaded. The Department manages every
    short on the site, as it moderates everything else.
    """
    rows = (
        Short.objects.all()
        if user.is_superuser
        else Short.objects.filter(author=user)
    )
    return rows.select_related("author")
