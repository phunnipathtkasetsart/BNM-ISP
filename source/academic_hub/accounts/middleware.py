from django.conf import settings
from django.shortcuts import redirect


GUEST_SESSION_KEY = "is_guest"
# The public board is the point of a guest session, so it heads the list.
GUEST_LANDING_VIEW = "announcements:public_board"

GUEST_ALLOWED_VIEWS = {
    GUEST_LANDING_VIEW,
    "accounts:logout",
    # The way out of guest mode. Blocking these made the "Sign in"
    # control in the bar bounce straight back to the board, which
    # left a guest with no route to an account at all. Register is
    # included because the sign-in page links to it - allowing one
    # without the other just moves the dead end one click along.
    "accounts:login",
    "accounts:register",
}


def is_guest_request(request):
    """Return True only for an anonymous browser session marked as a guest."""
    return (
        not request.user.is_authenticated
        and request.session.get(GUEST_SESSION_KEY, False) is True
    )


class GuestAccessMiddleware:
    """Keep guest sessions on the public board.

    Guests are deliberately session identities, not rows in the custom Users
    table. Centralising the restriction here means a guest cannot reach an
    existing or future application view merely by typing its URL.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not is_guest_request(request):
            return None

        static_path = f"/{settings.STATIC_URL.lstrip('/')}"
        if request.path.startswith(static_path):
            return None

        view_name = getattr(request.resolver_match, "view_name", None)
        if view_name in GUEST_ALLOWED_VIEWS:
            return None

        return redirect(GUEST_LANDING_VIEW)
