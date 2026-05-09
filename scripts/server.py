"""Zenigoke catalog server — FastAPI + static mount.

Replaces `python3 -m http.server`. Serves the existing `report/` tree at the
same URLs and adds /api/* endpoints (added in Tasks 2-4).

Env vars:
  ZENIGOKE_HOST            default 0.0.0.0
  ZENIGOKE_PORT            default 8088
  ZENIGOKE_REPORT_DIR      default report
  ZENIGOKE_DB_PATH         default db/kknmsmd.db
  ZENIGOKE_BUNDLES_DIR     default report/bundles
  ZENIGOKE_PUBLIC_BASE     default http://<host>:<port>  (URL prefix announced in track responses)
  ZENIGOKE_BUNDLES_PUBLIC  default <ZENIGOKE_PUBLIC_BASE>/bundles
  ZENIGOKE_CORS_ORIGIN     default *  (single origin or '*' — set to GitHub Pages URL in cloud mode)
"""
from __future__ import annotations
import os
import pathlib
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT_DIR = pathlib.Path(os.getenv("ZENIGOKE_REPORT_DIR", REPO_ROOT / "report"))
BUNDLES_DIR = pathlib.Path(os.getenv("ZENIGOKE_BUNDLES_DIR", REPORT_DIR / "bundles"))
DB_PATH = pathlib.Path(os.getenv("ZENIGOKE_DB_PATH", REPO_ROOT / "db" / "kknmsmd.db"))
CORS_ORIGIN = os.getenv("ZENIGOKE_CORS_ORIGIN", "*")

app = FastAPI(title="zenigoke", version="phase3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[CORS_ORIGIN] if CORS_ORIGIN != "*" else ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Routers added in later tasks:
#   from api_axes import router as axes_router; app.include_router(axes_router)
#   from api_matrix import router as matrix_router; app.include_router(matrix_router)
#   from api_bundle import router as bundle_router; app.include_router(bundle_router)

BUNDLES_DIR.mkdir(parents=True, exist_ok=True)

# Static fallback last, so /api routes win
app.mount("/", StaticFiles(directory=str(REPORT_DIR), html=True, follow_symlink=True), name="report")


def main() -> None:
    import uvicorn
    host = os.getenv("ZENIGOKE_HOST", "0.0.0.0")
    port = int(os.getenv("ZENIGOKE_PORT", "8088"))
    uvicorn.run("server:app", host=host, port=port, app_dir=str(REPO_ROOT / "scripts"))


if __name__ == "__main__":
    main()
