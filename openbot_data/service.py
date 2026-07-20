"""HTTP service wrapper for the OpenBot Data processor."""

from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from openbot_data.processor import ProcessingError, process_subtask_job


class SubtaskProcessorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    dataset_id: str
    source: Dict[str, Any]
    segmentation: Dict[str, Any]
    labeling: Dict[str, Any]
    task_hint: Optional[str] = None
    prompt_version: str = "subtask-timeline-v1"
    metadata: Optional[Dict[str, Any]] = None


app = FastAPI(title="OpenBot Data Processor", version="0.1.0")


def _authorize(authorization: Optional[str]) -> None:
    secret = os.getenv("OPENBOT_PROCESSOR_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="processor secret is not configured")
    expected = f"Bearer {secret}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid processor credentials")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "openbot-data-processor"}


@app.post("/v1/process/subtasks")
def process_subtasks(
    request: SubtaskProcessorRequest,
    authorization: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _authorize(authorization)
    try:
        return process_subtask_job(request.model_dump())
    except ProcessingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
