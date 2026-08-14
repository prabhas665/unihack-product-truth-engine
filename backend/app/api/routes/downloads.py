"""GET /api/downloads/{name}: serve generated delivery CSVs.

Only serves files inside the managed batch directory (never the official
reference files, never anything outside the designated folder): the name is
resolved inside BATCH_DIR and the result must still be under it - path
traversal is refused with 404.
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.routes import batch

router = APIRouter(prefix="/api", tags=["downloads"])


@router.get("/downloads/{name}")
def download(name: str) -> FileResponse:
    base = batch.BATCH_DIR.resolve()
    target = (base / name).resolve()
    if name in ("", ".", "..") or target.parent != base or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        target,
        media_type="text/csv; charset=utf-8",
        filename=name,
    )