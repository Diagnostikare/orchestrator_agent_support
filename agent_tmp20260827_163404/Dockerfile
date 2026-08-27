
FROM python:3.11-slim
WORKDIR /app

# Create a non-root user
RUN adduser --disabled-password --gecos "" myuser

# Switch to the non-root user
USER myuser

# Set up environment variables - Start
ENV PATH="/home/myuser/.local/bin:$PATH"

ENV GOOGLE_GENAI_USE_ENTERPRISE=1
ENV GOOGLE_CLOUD_PROJECT=architecture-beta
ENV GOOGLE_CLOUD_LOCATION=us-central1

# Set up environment variables - End

# Install ADK - Start
RUN pip install "google-adk[a2a]==2.7.1"
# Remove dev_server.py to ensure production-safe endpoints only (disabling dev endpoints in production)
RUN python -c "import os, glob, google.adk.cli as cli; d = os.path.dirname(cli.__file__); [os.remove(f) for f in glob.glob(os.path.join(d, 'dev_server*'))]; [os.remove(f) for f in glob.glob(os.path.join(d, '__pycache__', 'dev_server*'))]" || true
# Install ADK - End

# Copy agent - Start

# Set permission
COPY --chown=myuser:myuser "agents/agent/" "/app/agents/agent/"

# Copy agent - End

# Install Agent Deps - Start
RUN pip install -r "/app/agents/agent/requirements.txt"
# Install Agent Deps - End

EXPOSE 8080

CMD adk api_server --port=8080 --host=0.0.0.0 --session_service_uri=agentengine://projects/architecture-beta/locations/us-central1/reasoningEngines/6890865320911699968 --memory_service_uri=agentengine://projects/architecture-beta/locations/us-central1/reasoningEngines/6890865320911699968  --otel_to_cloud  --a2a  --gemini_enterprise_app_name=agent "/app/agents"
