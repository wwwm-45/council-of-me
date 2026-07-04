"""
FastAPI entry for Council of Me.
Run: uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    sessions,
    llm_models,
    elicitation,
    portrait,
    debate,
    interventions,
    synthesis,
    reflection,
    closure,
    history,
    report,
)
from app.services.llm import shutdown_clients


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await shutdown_clients()


app = FastAPI(
    title="Council of Me API",
    description="Multi-agent debate for self-reflection",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router)
app.include_router(llm_models.router)
app.include_router(elicitation.router)
app.include_router(portrait.router)
app.include_router(debate.router)
app.include_router(interventions.router)
app.include_router(synthesis.router)
app.include_router(reflection.router)
app.include_router(closure.router)
app.include_router(history.router)
app.include_router(report.router)


@app.get("/health")
def health():
    return {"status": "ok"}
