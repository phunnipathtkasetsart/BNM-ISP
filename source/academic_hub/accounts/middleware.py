from django.conf import settings
from django.shortcuts import redirect


GUEST_SESSION_KEY = "is_guest"
# The public board is the point of a guest session, so it heads the list.
GUEST_LANDING_VIEW = "announcements:public_board"

GUEST_ALLOWED_VIEWS = {
    GUEST_LANDING_VIEW,
    # Guests read the FAQ board too - they just cannot post on it.
    "announcements:faq_board",
    "accounts:logout",
    # The way out of guest mode; without both, a guest has no route to an account.
    "accounts:login",
    "accounts:register",
}


# How long a guest session lasts before it expires and the row is orphaned.
# Set per-session rather than through SESSION_COOKIE_AGE, which would log real
# students out on the same timer.
GUEST_SESSION_SECONDS = 30 * 60

# Guest accounts are real rows, so their IDs have to live in the same 10-char
# primary key as everyone else. "G" + 9 digits cannot collide with a ten-digit
# nisit ID (all digits) or a staff ID like A0001 (different letter, shorter).
GUEST_ID_PREFIX = "G"


def is_guest_request(request):
    """Return True for a browser session marked as a guest.

    A guest is signed in as a generated account, so authentication alone no
    longer distinguishes them - the session flag is what does.
    """
    return request.session.get(GUEST_SESSION_KEY, False) is True


class GuestAccessMiddleware:
    """Keep guest sessions on the public board.

    Centralised here so a guest cannot reach a future view by typing its URL.
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
