from fastapi import FastAPI

app = FastAPI(title="Clone My Professor")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
