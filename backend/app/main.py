from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import logging

from pathlib import Path, PureWindowsPath

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from .api.routes_ace_jobs import router as ace_jobs_router
from .api.routes_audio_exports import router as audio_exports_router
from .api.routes_audio_settings import router as audio_settings_router
from .api.routes_audio_versions import router as audio_versions_router
from .api.routes_lora_dataset import router as lora_dataset_router
from .api.routes_midi import router as midi_router
from .api.routes_orchestrator import router as orchestrator_router
from .api.routes_projects import router as projects_router
from .api.routes_proxy import router as proxy_router
from .api.routes_settings import router as settings_router
from .api.routes_stems import router as stems_router
from .api.routes_tracks import router as tracks_router
from .api.routes_videos import router as videos_router
from .api.routes_voices import router as voices_router
from .api.routes_voice_trials import router as voice_trials_router
from .api.routes_yue2_upload import router as yue2_upload_router
from .config import DATA_DIR, FRONTEND_DIST_DIR, LOG_DIR, SEED_VC_DIR, _LEGACY_LOG_DIR
from .data_root import ensure_layout, place_seed_models
from .orchestrator.manager import manager
from . import ace_jobs, audio_exports, audio_versions, midi, stems, video_jobs, voice_build, voice_comparisons


@asynccontextmanager
async def lifespan(app: FastAPI):
    legacy_logs = _LEGACY_LOG_DIR if LOG_DIR.resolve() == (DATA_DIR / "logs").resolve() else None
    ensure_layout(DATA_DIR, legacy_logs)
    place_seed_models(DATA_DIR, SEED_VC_DIR)
    await audio_versions.recover()
    await audio_exports.recover_exports()
    ace_jobs.recover()
    await video_jobs.recover()
    manager.start_watchdog()
    try:
        yield
    finally:
        # Drain one-shot jobs before stopping the engines they depend on.
        try:
            await ace_jobs.shutdown()
        finally:
            try:
                outcomes = await asyncio.gather(
                    voice_build.shutdown(), audio_exports.shutdown_exports(), voice_comparisons.shutdown(), video_jobs.shutdown(),
                    stems.shutdown(), midi.shutdown(), return_exceptions=True,
                )
                for outcome in outcomes:
                    if isinstance(outcome, BaseException):
                        logging.getLogger(__name__).error("Job cleanup failed", exc_info=outcome)
            finally:
                await manager.stop_all()


app = FastAPI(title="Remiqora", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic error inputs can contain secrets, bytes, or non-finite numbers.
    # Return stable field/error information without echoing those raw inputs.
    errors = [{"path": [str(part) for part in error["loc"]], "code": error["type"]} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "invalid_request", "errors": errors})

app.include_router(orchestrator_router)
app.include_router(ace_jobs_router)
app.include_router(audio_settings_router)
app.include_router(audio_versions_router)
app.include_router(audio_exports_router)
app.include_router(tracks_router)
app.include_router(stems_router)
app.include_router(midi_router)
app.include_router(projects_router)
app.include_router(lora_dataset_router)
app.include_router(settings_router)
app.include_router(voice_trials_router)
app.include_router(voices_router)
app.include_router(videos_router)
# Registered before proxy_router's catch-all so this exact path wins.
app.include_router(yue2_upload_router)
app.include_router(proxy_router)

if FRONTEND_DIST_DIR.exists():
    # Registered last so the API routes above always win. Serves a real file
    # from dist/ when one exists at that path (hashed JS/CSS under /assets,
    # favicon, etc.), otherwise falls back to index.html for the Vue router
    # to handle client-side (so a hard refresh on /ace-step still works).
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        root = FRONTEND_DIST_DIR.resolve()
        requested = Path(full_path)
        if requested.is_absolute() or PureWindowsPath(full_path).is_absolute() or ".." in requested.parts:
            raise HTTPException(status_code=404, detail="not_found")
        candidate = (root / requested).resolve()
        if not candidate.is_relative_to(root):
            raise HTTPException(status_code=404, detail="not_found")
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        index = (root / "index.html").resolve()
        if not index.is_relative_to(root) or not index.is_file():
            raise HTTPException(status_code=404, detail="not_found")
        return FileResponse(index)
