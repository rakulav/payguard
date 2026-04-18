FROM python:3.11.10-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential && \
    rm -rf /var/lib/apt/lists/*

# Layer 1: Core web framework
RUN pip install --no-cache-dir --quiet \
    "fastapi==0.115.6" \
    "uvicorn[standard]==0.34.0" \
    "strawberry-graphql[fastapi]==0.254.1" \
    "sse-starlette==2.2.1" \
    "pydantic==2.10.3" \
    "pydantic-settings==2.7.0" \
    "httpx==0.28.1" \
    "python-dotenv==1.0.1"

# Layer 2: Database & vector stores
RUN pip install --no-cache-dir --quiet \
    "sqlalchemy[asyncio]==2.0.36" \
    "asyncpg==0.30.0" \
    "psycopg2-binary==2.9.10" \
    "pgvector==0.3.6" \
    "qdrant-client==1.12.1" \
    "opensearch-py==2.8.0"

# Layer 3: LLM + agent orchestration
RUN pip install --no-cache-dir --quiet \
    "anthropic>=0.49,<1.0" \
    "langgraph>=0.2,<0.3" \
    "langchain-core>=0.3,<0.4"

# Layer 4: Embeddings (ONNX-based, NO PyTorch needed — fast install)
RUN pip install --no-cache-dir --quiet \
    "fastembed==0.4.2"

# Layer 5: Data & testing
RUN pip install --no-cache-dir --quiet \
    "pandas==2.2.3" \
    "pyarrow==18.1.0" \
    "numpy<2" \
    "faker==33.1.0" \
    "requests==2.32.3" \
    "tqdm==4.67.1" \
    "pytest==8.3.4" \
    "pytest-asyncio==0.24.0"

COPY . .

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
