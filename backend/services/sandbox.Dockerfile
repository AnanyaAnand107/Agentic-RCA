# Build in a trusted environment; candidate code is executed later with --network none.
# Build from the project root: docker build -f backend/services/sandbox.Dockerfile -t agentic-rca-sandbox:py311 .
FROM python:3.11-slim
WORKDIR /opt/agentic-rca
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt
WORKDIR /workspace
CMD ["python", "-m", "pytest", "-q"]
