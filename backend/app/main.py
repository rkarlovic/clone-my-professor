from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.admin import router as admin_router
from app.chat import router as chat_router
from app.courses import router as courses_router
from app.lti import router as lti_router
from app.notifications import router as notifications_router
from app.persona import router as persona_router
from app.questions import router as questions_router
from app.sync import router as sync_router
from jobs import close_queue, open_queue


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    open_queue()  # uploads enqueue ingest jobs
    try:
        yield
    finally:
        close_queue()


app = FastAPI(title="Clone My Professor", lifespan=lifespan)
app.include_router(courses_router)
app.include_router(chat_router)
app.include_router(questions_router)
app.include_router(persona_router)
app.include_router(lti_router)
app.include_router(sync_router)
app.include_router(notifications_router)
app.include_router(admin_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
