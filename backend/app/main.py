import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import Base, engine, add_missing_columns
from app.printing import backends, dispatcher
from app.routers import papers as papers_router
from app.routers import print_jobs as print_jobs_router
from app.routers import printer_agent as printer_agent_router
from app.routers import requests as requests_router

logging.basicConfig(level=logging.INFO)

Base.metadata.create_all(bind=engine)
add_missing_columns()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    dispatcher.start()
    yield
    dispatcher.stop()


app = FastAPI(
    title="AI Design Fulfillment Agent",
    description=(
        "Design flow (prompt -> image -> print-ready PDF) and mock paper flow "
        "(material -> blueprint/questions/answers -> verify + render), both "
        "feeding a confirmed, page-limited print queue."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/files", StaticFiles(directory=settings.storage_dir), name="files")

app.include_router(requests_router.router)
app.include_router(papers_router.router)
app.include_router(print_jobs_router.router)
app.include_router(printer_agent_router.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "groq_configured": not settings.is_placeholder(settings.groq_api_key),
        "gemini_configured": not settings.is_placeholder(settings.gemini_api_key),
        "firebase_enabled": settings.firebase_enabled,
        "moderation_configured": not settings.is_placeholder(settings.moderation_api_key),
        "auth_required": settings.auth_required,
        "shared_printer": settings.printer_backend,
        "shared_printer_online": backends.status()[0],
    }
