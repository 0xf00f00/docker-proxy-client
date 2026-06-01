from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# Vite emits content-hashed filenames under /assets (e.g. index-Bye3ZFFB.js), so those
# bytes never change for a given URL and can be cached forever. index.html is the mutable
# entry point that references them, so it must always be revalidated; ETag/Last-Modified
# (set automatically by Starlette's FileResponse) keep that revalidation a cheap 304.
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"
DEFAULT = "public, max-age=86400, must-revalidate"


class CachedStaticFiles(StaticFiles):
    """StaticFiles that sets Cache-Control based on the resolved asset."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/html"):
            # index.html (incl. the html=True SPA fallback): never serve stale.
            response.headers["Cache-Control"] = NO_CACHE
        elif path.startswith("assets/"):
            response.headers["Cache-Control"] = IMMUTABLE
        else:
            # Non-hashed root files (icons, manifest): short cache, revalidate.
            response.headers["Cache-Control"] = DEFAULT
        return response
