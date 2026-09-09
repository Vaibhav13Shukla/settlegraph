from fastapi import FastAPI

app = FastAPI(title="SettleGraph API")


@app.get("/")
def home() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "SettleGraph API is running",
    }


@app.get("/healthz")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "message": "SettleGraph API is running",
    }
