"""Public API documentation using pinned local bundles and per-request nonces."""

import json
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)
_HTML_ROUTE_METHODS = ["GET", "HEAD"]
SWAGGER = "/static/vendor/swagger-ui-5.34.1"
REDOC = "/static/vendor/redoc-2.5.4"
SYSTEM = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"


def _document(request: Request, title: str, body: str) -> HTMLResponse:
    """Wrap documentation content in an HTML document with the local site favicon."""
    root = escape(request.scope.get("root_path", "").rstrip("/"), quote=True)
    return HTMLResponse(
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(request.app.title)} - {title}</title>"
        f'<link rel="icon" href="{root}/static/favicon/favicon-96x96.png">'
        f"</head><body>{body}</body></html>"
    )


@router.api_route("/docs", methods=_HTML_ROUTE_METHODS)
async def swagger_ui(request: Request) -> HTMLResponse:
    """Serve Swagger UI with local assets and a nonce-protected initializer."""
    nonce = request.state.csp_nonce
    root = request.scope.get("root_path", "").rstrip("/")
    assets = escape(root + SWAGGER, quote=True)
    options = json.dumps(
        {
            "url": root + request.app.openapi_url,
            "dom_id": "#swagger-ui",
            "layout": "BaseLayout",
            "deepLinking": True,
            "showExtensions": True,
            "showCommonExtensions": True,
            "validatorUrl": None,
        }
    )
    redirect = json.dumps(root + "/docs/oauth2-redirect")
    return _document(
        request,
        "Swagger UI",
        f'<link rel="stylesheet" href="{assets}/swagger-ui.css">'
        '<div id="swagger-ui"></div>'
        f'<script src="{assets}/swagger-ui-bundle.js" nonce="{nonce}"></script>'
        f'<script nonce="{nonce}">const ui = SwaggerUIBundle({{...{options},'
        f"oauth2RedirectUrl: window.location.origin + {redirect},"
        "presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset]"
        "});</script>",
    )


@router.api_route("/docs/oauth2-redirect", methods=_HTML_ROUTE_METHODS)
async def oauth2_redirect(request: Request) -> HTMLResponse:
    """Serve the local Swagger OAuth callback without inline executable code."""
    nonce = request.state.csp_nonce
    root = escape(request.scope.get("root_path", "").rstrip("/"), quote=True)
    return HTMLResponse(
        '<!doctype html><html><head><meta charset="utf-8"></head><body>'
        f'<script src="{root}{SWAGGER}/oauth2-redirect.js" nonce="{nonce}"></script>'
        "</body></html>"
    )


@router.api_route("/redoc", methods=_HTML_ROUTE_METHODS)
async def redoc(request: Request) -> HTMLResponse:
    """Serve ReDoc with local assets, system fonts and nonce-aware generated styles."""
    nonce = request.state.csp_nonce
    root = request.scope.get("root_path", "").rstrip("/")
    assets = escape(root + REDOC, quote=True)
    url = json.dumps(root + request.app.openapi_url)
    options = json.dumps(
        {
            "nonce": nonce,
            "theme": {
                "typography": {
                    "fontFamily": SYSTEM,
                    "headings": {"fontFamily": SYSTEM},
                    "code": {"fontFamily": MONO},
                }
            },
        }
    )
    return _document(
        request,
        "ReDoc",
        f'<style nonce="{nonce}">body{{margin:0;padding:0}}</style>'
        '<div id="redoc-container"></div>'
        f'<script src="{assets}/redoc.standalone.js" nonce="{nonce}"></script>'
        f'<script nonce="{nonce}">Redoc.init({url}, {options}, '
        'document.getElementById("redoc-container"));</script>',
    )
