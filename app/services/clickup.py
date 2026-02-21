"""ClickUp API client – fetch tasks, read custom fields, post comments."""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


@dataclass
class TaskInfo:
    """Structured information extracted from a ClickUp task."""

    task_id: str
    task_name: str
    description: str
    github_repo: str | None = None
    github_branch: str | None = None
    create_pr: bool = False
    pr_target_branch: str | None = None


class ClickUpClient:
    """Thin async wrapper around the ClickUp v2 REST API."""

    def __init__(self, settings: Settings) -> None:
        self._token = settings.clickup_api_token
        self._webhook_secret = settings.clickup_webhook_secret
        self._headers = {
            "Authorization": self._token,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Webhook signature verification
    # ------------------------------------------------------------------
    def verify_webhook_signature(self, raw_body: bytes, signature: str) -> bool:
        """Return *True* if the HMAC-SHA256 signature matches."""
        if not self._webhook_secret:
            logger.warning("CLICKUP_WEBHOOK_SECRET not set – skipping verification")
            return True
        expected = hmac.new(
            self._webhook_secret.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Fetch task
    # ------------------------------------------------------------------
    async def get_task(self, task_id: str) -> TaskInfo:
        """Fetch a task by ID and extract relevant custom fields."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{CLICKUP_API_BASE}/task/{task_id}",
                headers=self._headers,
                params={"include_markdown_description": "true"},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        custom = _extract_custom_fields(data.get("custom_fields", []))
        return TaskInfo(
            task_id=data["id"],
            task_name=data.get("name", ""),
            description=data.get("markdown_description") or data.get("description", ""),
            github_repo=custom.get("github_repo"),
            github_branch=custom.get("github_branch"),
            create_pr=custom.get("create_pr", False),
            pr_target_branch=custom.get("pr_target_branch"),
        )

    # ------------------------------------------------------------------
    # Post comment back to task
    # ------------------------------------------------------------------
    async def post_comment(self, task_id: str, comment_text: str) -> None:
        """Post a plain-text comment on the given ClickUp task."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{CLICKUP_API_BASE}/task/{task_id}/comment",
                headers=self._headers,
                json={"comment_text": comment_text},
            )
            resp.raise_for_status()
        logger.info("Posted comment on ClickUp task %s", task_id)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# Mapping from custom-field *name* (lowercased) to the key we use internally.
_FIELD_MAP: dict[str, str] = {
    "github_repo": "github_repo",
    "github repo": "github_repo",
    "github_branch": "github_branch",
    "github branch": "github_branch",
    "create_pr": "create_pr",
    "create pr": "create_pr",
    "pr_target_branch": "pr_target_branch",
    "pr target branch": "pr_target_branch",
}


def _extract_custom_fields(fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull known custom-field values out of the ClickUp field list."""
    result: dict[str, Any] = {}
    for field in fields:
        name = (field.get("name") or "").strip().lower()
        key = _FIELD_MAP.get(name)
        if key is None:
            continue

        value = field.get("value")

        # Checkbox / boolean fields come as "true" / True / 1 etc.
        if key == "create_pr":
            result[key] = _to_bool(value)
        else:
            if isinstance(value, str):
                result[key] = value.strip() or None
            else:
                result[key] = value
    return result


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return False
