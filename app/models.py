from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# ClickUp webhook payload (subset of fields we care about)
# ---------------------------------------------------------------------------
class ClickUpWebhookPayload(BaseModel):
    event: str
    task_id: str
    webhook_id: str | None = None
    history_items: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Direct API request
# ---------------------------------------------------------------------------
class PushCodeRequest(BaseModel):
    """Body for the POST /api/push-code endpoint."""

    task_id: str | None = None
    # When task_id is provided the service fetches everything from ClickUp.
    # Alternatively, the caller can supply the payload directly:
    github_repo: str | None = None          # "owner/repo"
    github_branch: str | None = None        # target branch (must exist)
    files: dict[str, str] | None = None     # {filepath: content}
    commit_message: str | None = None
    create_pr: bool = False
    pr_target_branch: str | None = None     # base branch for the PR


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------
class PushCodeResponse(BaseModel):
    success: bool
    commit_sha: str | None = None
    commit_url: str | None = None
    pr_url: str | None = None
    message: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
