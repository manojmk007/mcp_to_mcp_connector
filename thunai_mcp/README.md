# Thunai MCP Integration Guide

This guide provides instructions for deploying and integrating your FastAPI server with the [Thunai MCP platform](https://mcp.thunai.ai/).

## 🚀 Running Locally

To run the server locally for testing:

```bash
# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn app:app --host 0.0.0.0 --port 8000
```

*   **OpenAPI URL:** `http://localhost:8000/openapi.json`
*   **Swagger Docs:** `http://localhost:8000/docs`

---

## 🐳 Docker Deployment

### Build the Image
```bash
docker build -t thunai-mcp-server .
```

### Run the Container
```bash
docker run -d -p 8000:8000 --name thunai-mcp thunai-mcp-server
```

---

## 📡 Sample Request (cURL)

You can verify the endpoint is working correctly using the following command:

```bash
curl -X 'POST' \
  'http://localhost:8000/process' \
  -H 'accept: application/json' \
  -H 'Content-Type: application/json' \
  -d '{
  "message": "Hello from Thunai"
}'
```

**Expected Response:**
```json
{
  "status": "success",
  "response": "Processed -> Hello from Thunai"
}
```

---

## 🛠️ Thunai Integration

To integrate this with Thunai MCP:

1.  Go to [https://mcp.thunai.ai/](https://mcp.thunai.ai/).
2.  Select **"Add Custom Tool"** or **"Import OpenAPI"**.
3.  Provide the public URL to your `openapi.json`:
    `http://YOUR_SERVER_IP:8000/openapi.json`
4.  Thunai will automatically parse the `/process` endpoint and make it available as a tool for the AI.

### Example Thunai Tool Configuration (Manual)
If you need to configure it manually in a JSON structure:

```json
{
  "name": "process_message",
  "description": "Processes a message through the custom MCP server.",
  "parameters": {
    "type": "object",
    "properties": {
      "message": {
        "type": "string",
        "description": "The message to be processed"
      }
    },
    "required": ["message"]
  }
}
```

---

## 📝 Key Features Implemented

*   **Async Support:** Built on FastAPI's native `async def` for high-concurrency performance.
*   **Production Logging:** Middleware logs every request, method, path, and processing time.
*   **OpenAPI 3.0:** Fully compliant with OpenAPI standards for seamless tool discovery.
*   **Production-Ready Docker:** Multi-stage-like lean Dockerfile with environment optimizations.
