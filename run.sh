#!/usr/bin/env bash
set -euo pipefail

echo "PayGuard: LLM-Powered Payment Fraud Investigation Assistant"
echo "============================================================"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
    echo "✗ Docker not found. Please install Docker Desktop and try again."
    exit 1
fi

if ! docker info &> /dev/null 2>&1; then
    echo "✗ Docker daemon not running. Please start Docker Desktop and try again."
    exit 1
fi

echo "✓ Docker is running"

# Check for .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo "✓ Created .env from .env.example"
fi

# Check for API key
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
    if grep -q "sk-ant-your-key-here" .env 2>/dev/null; then
        echo "⚠ No ANTHROPIC_API_KEY set. Running with MOCK_LLM=1 for demo."
        sed -i.bak 's/MOCK_LLM=0/MOCK_LLM=1/' .env 2>/dev/null || \
        sed -i '' 's/MOCK_LLM=0/MOCK_LLM=1/' .env
        rm -f .env.bak
    fi
else
    # Write the key into .env
    sed -i.bak "s|sk-ant-your-key-here|${ANTHROPIC_API_KEY}|" .env 2>/dev/null || \
    sed -i '' "s|sk-ant-your-key-here|${ANTHROPIC_API_KEY}|" .env
    rm -f .env.bak
    echo "✓ ANTHROPIC_API_KEY configured"
fi

echo ""
echo "Starting PayGuard..."
echo ""

make demo
