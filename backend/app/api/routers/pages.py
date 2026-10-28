"""Страницы клиента: Login.html и cryptis.html отдаются тем же backend на :3890 (Ports_and_Database.md, 1)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

router = APIRouter(include_in_schema=False)

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
STATIC = Path(__file__).resolve().parents[3] / "static"


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/login")


@router.get("/login")
async def login_page() -> FileResponse:
    return FileResponse(TEMPLATES / "login.html", media_type="text/html")


@router.get("/app")
@router.get("/app/{screen:path}")
async def app_page(screen: str = "") -> FileResponse:
    """Каждый экран клиента — свой адрес (/app/settings, /app/new-message, /app/chat/<id>);
    страница одна, нужный экран по адресу выбирает static/js/app/router.js."""
    return FileResponse(TEMPLATES / "cryptis.html", media_type="text/html")


@router.get("/sw.js")
async def service_worker() -> FileResponse:
    """Service worker Web Push. Отдаётся с корня, чтобы его область покрывала /app/…;
    no-cache — браузер сверяет новую версию при каждой регистрации."""
    return FileResponse(STATIC / "js" / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@router.get("/tonconnect-manifest.json")
async def tonconnect_manifest(request: Request) -> JSONResponse:
    origin = request.app.state.infra.settings.public_origin
    return JSONResponse({
        "url": origin,
        "name": "CryptisDchat",
        "iconUrl": f"{origin}/static/images/icon-180.png",
        "termsOfUseUrl": f"{origin}/login",
        "privacyPolicyUrl": f"{origin}/login",
    })


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
