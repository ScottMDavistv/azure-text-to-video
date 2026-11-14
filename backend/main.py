from fastapi import FastAPI

app = FastAPI(
    title="Azure Text-to-Video API",
    description="Backend API for cinematic AI video generation",
    version="0.1.0",
)


@app.get("/")
def root():
    return {
        "application": "Azure Text-to-Video",
        "status": "running",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "textvideo-api",
    }
