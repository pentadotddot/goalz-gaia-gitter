"""ClickUp ↔ GitHub Code Bridge – FastAPI application."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.models import (
    ClickUpWebhookPayload,
    HealthResponse,
    PushCodeRequest,
    PushCodeResponse,
)
from app.services.clickup import ClickUpClient
from app.services.github import GitHubService
from app.services.parser import parse_code_blocks

from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
app = FastAPI(
    title="ClickUp-GitHub Code Bridge",
    description=(
        "Receives code from ClickUp tasks and commits it to GitHub branches."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Lazy-initialised service singletons
# ---------------------------------------------------------------------------
_clickup_client: ClickUpClient | None = None
_github_service: GitHubService | None = None


def _get_clickup() -> ClickUpClient:
    global _clickup_client
    if _clickup_client is None:
        _clickup_client = ClickUpClient(get_settings())
    return _clickup_client


def _get_github() -> GitHubService:
    global _github_service
    if _github_service is None:
        _github_service = GitHubService(get_settings())
    return _github_service


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


# ---------------------------------------------------------------------------
# POST /webhook/clickup – ClickUp webhook receiver
# ---------------------------------------------------------------------------
@app.post("/webhook/clickup", response_model=PushCodeResponse)
async def webhook_clickup(
    request: Request,
    x_signature: str | None = Header(None),
) -> PushCodeResponse:
    raw_body = await request.body()
    clickup = _get_clickup()

    # Signature verification
    if x_signature and not clickup.verify_webhook_signature(raw_body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = ClickUpWebhookPayload.model_validate_json(raw_body)

    # We only act on task-updated events that look relevant
    if payload.event not in ("taskUpdated", "taskCreated"):
        return PushCodeResponse(success=True, message=f"Ignored event: {payload.event}")

    task_id = payload.task_id
    logger.info("Webhook received for task %s (event=%s)", task_id, payload.event)

    return await _process_task(task_id)


# ---------------------------------------------------------------------------
# GET /api/check-repo – verify token access to a repo
# ---------------------------------------------------------------------------
class CheckRepoResponse(BaseModel):
    full_name: str
    default_branch: str
    private: bool
    permissions: dict


@app.get("/api/check-repo", response_model=CheckRepoResponse)
async def check_repo(repo: str) -> CheckRepoResponse:
    """Verify the GitHub token can access the given repo.

    Usage: ``GET /api/check-repo?repo=goalz-cons/my-project``
    """
    gh = _get_github()
    try:
        info = gh.check_repo_access(repo)
        return CheckRepoResponse(**info)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Cannot access repo: {exc}") from exc


# ---------------------------------------------------------------------------
# POST /api/push-code – direct API trigger / ClickUp automation webhook
# ---------------------------------------------------------------------------
@app.post("/api/push-code", response_model=PushCodeResponse)
async def push_code(
    request: Request,
    task_id: str | None = Query(None, description="ClickUp task ID (from URL query param)"),
) -> PushCodeResponse:
    # Try to parse the body as our PushCodeRequest; if it fails (e.g.
    # ClickUp automation sends its own payload), just use an empty one.
    body: PushCodeRequest | None = None
    try:
        raw = await request.body()
        if raw and raw.strip():
            body = PushCodeRequest.model_validate_json(raw)
    except Exception:
        logger.info("Could not parse request body as PushCodeRequest – using query params only")
        body = None

    if body is None:
        body = PushCodeRequest()

    # Query-param task_id takes priority over body task_id
    effective_task_id = task_id or body.task_id

    # Path A: task_id provided → fetch everything from ClickUp
    if effective_task_id:
        return await _process_task(
            task_id=effective_task_id,
            override_repo=body.github_repo,
            override_branch=body.github_branch,
            override_base_branch=body.base_branch,
            override_files=body.files,
            override_commit_msg=body.commit_message,
            override_create_pr=body.create_pr,
            override_pr_target=body.pr_target_branch,
        )

    # Path B: caller supplies everything directly (no ClickUp fetch)
    if not body.github_repo or not body.github_branch or not body.files:
        raise HTTPException(
            status_code=400,
            detail=(
                "When task_id is not provided you must supply "
                "github_repo, github_branch, and files."
            ),
        )

    return _commit_and_maybe_pr(
        repo=body.github_repo,
        branch=body.github_branch,
        base_branch=body.base_branch,
        files=body.files,
        commit_message=body.commit_message or "Code push via API",
        create_pr=body.create_pr,
        pr_target_branch=body.pr_target_branch,
    )


# ---------------------------------------------------------------------------
# Shared orchestration helpers
# ---------------------------------------------------------------------------
async def _process_task(
    task_id: str,
    *,
    override_repo: str | None = None,
    override_branch: str | None = None,
    override_base_branch: str | None = None,
    override_files: dict[str, str] | None = None,
    override_commit_msg: str | None = None,
    override_create_pr: bool = False,
    override_pr_target: str | None = None,
) -> PushCodeResponse:
    """Fetch a ClickUp task, parse its code blocks, and push to GitHub."""
    clickup = _get_clickup()
    settings = get_settings()

    task = await clickup.get_task(task_id)

    repo = override_repo or task.github_repo
    branch = override_branch or task.github_branch
    files = override_files or parse_code_blocks(task.description)
    create_pr = override_create_pr or task.create_pr
    pr_target = override_pr_target or task.pr_target_branch

    if not repo:
        raise HTTPException(status_code=400, detail="github_repo not set on task or request")
    if not branch:
        raise HTTPException(status_code=400, detail="github_branch not set on task or request")
    if not files:
        raise HTTPException(status_code=400, detail="No code blocks found in task description")

    commit_message = override_commit_msg or settings.commit_message_template.format(
        task_id=task.task_id,
        task_name=task.task_name,
    )

    result = _commit_and_maybe_pr(
        repo=repo,
        branch=branch,
        base_branch=override_base_branch,
        files=files,
        commit_message=commit_message,
        create_pr=create_pr,
        pr_target_branch=pr_target,
    )

    # Post a comment back to ClickUp with the result
    comment_parts = [f"Code pushed to `{branch}` — commit [`{result.commit_sha[:8]}`]({result.commit_url})"]
    if result.branch_created:
        comment_parts.append(f"Branch `{branch}` was auto-created.")
    if result.pr_url:
        comment_parts.append(f"Pull request: {result.pr_url}")
    try:
        await clickup.post_comment(task_id, "\n".join(comment_parts))
    except Exception:
        logger.exception("Failed to post comment back to ClickUp task %s", task_id)

    return result


def _commit_and_maybe_pr(
    *,
    repo: str,
    branch: str,
    base_branch: str | None = None,
    files: dict[str, str],
    commit_message: str,
    create_pr: bool,
    pr_target_branch: str | None,
) -> PushCodeResponse:
    """Commit files and optionally open a PR."""
    gh = _get_github()

    try:
        commit = gh.commit_files(
            repo_full_name=repo,
            branch=branch,
            files=files,
            commit_message=commit_message,
            base_branch=base_branch,
        )
    except Exception as exc:
        logger.exception("GitHub commit failed")
        raise HTTPException(status_code=502, detail=f"GitHub commit failed: {exc}") from exc

    pr_url: str | None = None
    if create_pr and pr_target_branch:
        try:
            pr_url = gh.create_pull_request(
                repo_full_name=repo,
                head_branch=branch,
                base_branch=pr_target_branch,
                title=commit_message,
                body=f"Automated PR from ClickUp-GitHub bridge.\n\nCommit: {commit.sha}",
            )
        except Exception:
            logger.exception("PR creation failed (commit was successful)")

    return PushCodeResponse(
        success=True,
        commit_sha=commit.sha,
        commit_url=commit.url,
        pr_url=pr_url,
        branch_created=commit.branch_created,
        message="Code committed successfully",
    )
