"""FastAPI app: `/ask`, `/feedback`, `/healthz`, `/version` and two static pages (`/`, `/how`).

Handlers are plain functions, so FastAPI runs them in its threadpool; the Service serializes
what must be serialized. `create_app` takes a factory so tests can build the Service from fakes;
`frl serve` builds it from `configs/serve.toml`.
"""

import html
import logging
import os
import signal
import threading
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from filings_rag.serve.limits import Refused
from filings_rag.serve.service import QuestionTooLong, Service

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
COMPANIES_SLOT = "<!-- companies -->"
# The tab icon is the header logo; browsers ask for /favicon.ico on their own.
ICONS = {
    "favicon.svg": "image/svg+xml",
    "favicon.ico": "image/x-icon",
    "apple-touch-icon.png": "image/png",
}


def index_page(companies: list[str]) -> str:
    """The ask page with the covered companies filled in from `corpus/companies.csv`."""
    items = "".join(f"<li>{html.escape(c)}</li>" for c in sorted(companies, key=str.casefold))
    return (STATIC / "index.html").read_text().replace(COMPANIES_SLOT, items)


def icon_route(name: str, media_type: str) -> Callable[[], FileResponse]:
    """A handler with no parameters: FastAPI turns any parameter, even a defaulted one,
    into a query parameter, so a bound `name` would let a client pick the file."""

    def icon() -> FileResponse:
        return FileResponse(STATIC / name, media_type=media_type)

    return icon


def exit_soon() -> None:
    """Shut down a moment after the current response is sent: SIGTERM, so uvicorn stops
    gracefully (the lifespan closes the Service and flushes traces). Docker restarts it."""
    threading.Timer(1.0, os.kill, (os.getpid(), signal.SIGTERM)).start()


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)  # the Service applies the real limit


class FeedbackBody(BaseModel):
    query_id: uuid.UUID
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


def create_app(
    factory: Callable[[], Service], on_index_lost: Callable[[], None] = exit_soon
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.service = factory()
        try:
            yield
        finally:
            app.state.service.close()

    app = FastAPI(title="Filings RAG Lab", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.exiting = False

    @app.middleware("http")
    async def restart_when_the_index_is_lost(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        svc: Service | None = getattr(request.app.state, "service", None)
        if svc is not None and not app.state.exiting and svc.index_lost():
            app.state.exiting = True
            log.error("lost the index connection; exiting so Docker restarts the api")
            on_index_lost()
        return response

    def service(request: Request) -> Service:
        svc: Service = request.app.state.service
        return svc

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return HTMLResponse(index_page(service(request).companies))

    @app.get("/how", include_in_schema=False)
    def how() -> FileResponse:
        return FileResponse(STATIC / "how.html")

    @app.get("/site.css", include_in_schema=False)
    def site_css() -> FileResponse:
        return FileResponse(STATIC / "site.css", media_type="text/css")

    @app.get("/markdown.js", include_in_schema=False)
    def markdown_js() -> FileResponse:
        return FileResponse(STATIC / "markdown.js", media_type="text/javascript")

    for name, media_type in ICONS.items():
        app.add_api_route(f"/{name}", icon_route(name, media_type), include_in_schema=False)

    @app.post("/ask")
    def ask(body: AskBody, request: Request) -> JSONResponse:
        # Behind Caddy, uvicorn's proxy headers make this the client's address.
        client = request.client.host if request.client else None
        try:
            asked = service(request).ask(
                body.question, client_ip=client, smoke_token=request.headers.get("x-smoke-token")
            )
        except QuestionTooLong as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        except Refused as e:
            headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
            return JSONResponse(
                {"status": "refused", "refusal": e.kind, "message": e.message},
                status_code=e.status_code,
                headers=headers,
            )
        return JSONResponse(asked.to_json(), status_code=502 if asked.status == "error" else 200)

    @app.post("/feedback", status_code=204)
    def feedback(body: FeedbackBody, request: Request) -> Response:
        if not service(request).feedback(body.query_id, body.rating, body.comment):
            raise HTTPException(status_code=404, detail="unknown query_id")
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz(request: Request) -> JSONResponse:
        try:
            return JSONResponse(service(request).health())
        except Exception as e:
            return JSONResponse({"ok": False, "error": type(e).__name__}, status_code=503)

    @app.get("/version")
    def version(request: Request) -> dict[str, str]:
        svc = service(request)
        return {
            "sha": svc.git_sha,
            "config_hash": svc.config_hash,
            "pipeline": svc.pipeline,
            "model": getattr(svc.backend, "model", ""),
        }

    return app
