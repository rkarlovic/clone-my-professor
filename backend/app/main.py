from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.chat import router as chat_router
from app.courses import router as courses_router
from app.questions import router as questions_router
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
