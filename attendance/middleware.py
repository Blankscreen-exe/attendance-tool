from . import services


class CloseStaleEntriesMiddleware:
    """Flags yesterday's forgotten clock-outs before any page is built.

    Doing it on each request means no scheduler has to be running for the
    record to be right; the check is a single indexed query.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            services.close_stale_entries()
        return self.get_response(request)
