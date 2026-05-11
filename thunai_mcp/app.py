import logging
import time
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field

# --- Logging Configuration ---
# Configured to provide visibility into incoming requests from Thunai
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("thunai-mcp")

# --- App Metadata ---
app = FastAPI(
    title="Thunai MCP Integration Server",
    description="A production-ready FastAPI server for integrating custom tools with the Thunai MCP platform.",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Force OpenAPI version to 3.0.2 for compatibility with Thunai MCP
    openapi_schema["openapi"] = "3.0.2"
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi

# --- Models ---
class ProcessRequest(BaseModel):
    """Request body for the /process endpoint."""
    message: str = Field(..., example="Hello Thunai", description="The message to be processed")

class ProcessResponse(BaseModel):
    """Response body for the /process endpoint."""
    status: str = Field(..., example="success")
    response: str = Field(..., example="Processed -> Hello Thunai")

# --- Middleware for Logging ---
@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log every incoming call to the server."""
    start_time = time.time()
    path = request.url.path
    method = request.method
    
    logger.info(f"Incoming {method} request to {path}")
    
    response = await call_next(request)
    
    process_time = time.time() - start_time
    logger.info(f"Completed {method} {path} in {process_time:.4f}s with status {response.status_code}")
    
    return response

# --- Endpoints ---

@app.get("/", include_in_schema=False)
async def root():
    """Redirect or provide basic info at root."""
    return {
        "message": "Thunai MCP Server is running",
        "docs": "/docs",
        "openapi": "/openapi.json"
    }

@app.post("/process", response_model=ProcessResponse, tags=["Tools"])
async def process_message(data: ProcessRequest):
    """
    Core processing endpoint for Thunai integration.
    
    This endpoint receives a message and returns a processed string.
    The implementation is async-ready for production workloads.
    """
    logger.info(f"Processing message: {data.message}")
    
    # Simulate some async work if needed (e.g., DB call, external API)
    # await asyncio.sleep(0.1) 
    
    processed_result = f"Processed -> {data.message}"
    
    return ProcessResponse(
        status="success",
        response=processed_result
    )

# --- Startup/Shutdown Events ---
@app.on_event("startup")
async def startup_event():
    logger.info("Thunai MCP Server starting up...")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Thunai MCP Server shutting down...")

if __name__ == "__main__":
    import uvicorn
    # This block allows running the script directly for debugging
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
