# Vercel Python (ASGI) entrypoint for the GT backbone.
import os
import sys
import traceback

from fastapi import FastAPI

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # backend/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

os.environ.setdefault("PARAMS_PATH", os.path.join(ROOT, "params", "params.yaml"))
os.environ.setdefault("COCKPIT_REPO", "supabase")
os.environ.setdefault("AUTH_MODE", "demo")

# Unconditional top-level `app` so Vercel's @vercel/python detects the ASGI app.
app: FastAPI = FastAPI()

try:
    from app.main import app as _real_app

    app = _real_app
except Exception:  # pragma: no cover - diagnostic surface for serverless import failures
    _tb = traceback.format_exc()
    _diag = {
        "import_error": _tb,
        "cwd": os.getcwd(),
        "params_path": os.environ.get("PARAMS_PATH"),
        "params_exists": os.path.exists(os.environ.get("PARAMS_PATH", "")),
        "root_listing": sorted(os.listdir(ROOT))[:40],
    }

    @app.get("/{full_path:path}")
    def _import_error(full_path: str):
        return _diag
