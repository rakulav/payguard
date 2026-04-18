"""FastAPI app: mounts REST + GraphQL + SSE."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter

from app.db import init_db
from app.rest_routes import router as rest_router
from app.graphql_schema import schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(
    title="PayGuard API",
    description="LLM-Powered Payment Fraud Investigation Assistant",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rest_router, prefix="/api")

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")
