"""What a phone needs to offer "Install app" / "Add to Home Screen" for this site.

Installing only adds an icon that opens the site full-screen. Nothing works
offline, on purpose: the clock button has to reach the server, because the
server is what records the time.
"""

from django.http import HttpResponse, JsonResponse
from django.templatetags.static import static
from django.views.decorators.cache import cache_control

THEME_COLOR = "#0f172a"

SERVICE_WORKER = """\
// Present only so that browsers offer to install the site as an app.
// It stores nothing: every page and every clock-in goes to the server.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
"""


def _icon(name, size, purpose="any"):
    return {
        "src": static(f"attendance/icons/{name}"),
        "sizes": f"{size}x{size}",
        "type": "image/png",
        "purpose": purpose,
    }


@cache_control(max_age=3600)
def manifest(request):
    return JsonResponse(
        {
            "name": "Attendance",
            "short_name": "Attendance",
            "description": "Clock in and out.",
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f8fafc",
            "theme_color": THEME_COLOR,
            "icons": [
                _icon("icon-192.png", 192),
                _icon("icon-512.png", 512),
                _icon("icon-maskable-512.png", 512, purpose="maskable"),
            ],
        },
        content_type="application/manifest+json",
    )


@cache_control(no_cache=True)
def service_worker(request):
    # Served from the top of the site so that it covers every page.
    return HttpResponse(SERVICE_WORKER, content_type="text/javascript")
