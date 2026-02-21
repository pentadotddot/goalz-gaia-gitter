"""GitHub service – commit files via the Git Data API, optionally create PRs.

All operations use the REST API through PyGithub so the service stays fully
stateless (no local git clone required).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from github import Auth, Github, GithubException, InputGitTreeElement

from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class CommitResult:
    sha: str
    url: str
    pr_url: str | None = None
    branch_created: bool = False


class GitHubService:
    """High-level helper that wraps PyGithub for our specific use-case."""

    def __init__(self, settings: Settings) -> None:
        auth = Auth.Token(settings.github_token)
        self._gh = Github(auth=auth)

    # ------------------------------------------------------------------
    # Verify token has access to a repo
    # ------------------------------------------------------------------
    def check_repo_access(self, repo_full_name: str) -> dict:
        """Return basic repo info to verify the token has access."""
        repo = self._gh.get_repo(repo_full_name)
        return {
            "full_name": repo.full_name,
            "default_branch": repo.default_branch,
            "private": repo.private,
            "permissions": {
                "admin": repo.permissions.admin if repo.permissions else False,
                "push": repo.permissions.push if repo.permissions else False,
                "pull": repo.permissions.pull if repo.permissions else False,
            },
        }

    # ------------------------------------------------------------------
    # Core: commit a set of files onto a branch
    # ------------------------------------------------------------------
    def commit_files(
        self,
        repo_full_name: str,
        branch: str,
        files: dict[str, str],
        commit_message: str,
        base_branch: str | None = None,
    ) -> CommitResult:
        """Create a single commit that writes *files* to *branch*.

        Parameters
        ----------
        repo_full_name:
            ``"owner/repo"`` identifier.
        branch:
            Target branch name. If it doesn't exist it will be created
            from *base_branch* (or the repo default branch).
        files:
            ``{file_path: file_content}`` mapping.
        commit_message:
            Commit message string.
        base_branch:
            Branch to fork from when *branch* doesn't exist yet.
            Defaults to the repo's default branch (usually ``main``).

        Returns
        -------
        CommitResult with the new commit SHA, URL, and whether the branch
        was freshly created.
        """
        repo = self._gh.get_repo(repo_full_name)

        # --- Ensure branch exists (create from base if needed) -----------
        branch_created = False
        try:
            ref = repo.get_git_ref(f"heads/{branch}")
        except GithubException as exc:
            if exc.status != 404:
                raise
            # Branch does not exist – create from base
            source = base_branch or repo.default_branch
            base_ref = repo.get_git_ref(f"heads/{source}")
            repo.create_git_ref(
                ref=f"refs/heads/{branch}",
                sha=base_ref.object.sha,
            )
            logger.info("Created branch %s from %s on %s", branch, source, repo_full_name)
            branch_created = True
            # Re-fetch the newly created ref
            ref = repo.get_git_ref(f"heads/{branch}")

        # 1. Get the SHA of the latest commit on the branch
        if ref.object is None:
            # Safety: re-fetch if the ref object wasn't populated
            ref = repo.get_git_ref(f"heads/{branch}")

        latest_commit_sha = ref.object.sha
        latest_commit = repo.get_git_commit(latest_commit_sha)
        base_tree = latest_commit.tree

        # 2. Build tree elements for every file
        tree_elements: list[InputGitTreeElement] = []
        for path, content in files.items():
            tree_elements.append(
                InputGitTreeElement(
                    path=path,
                    mode="100644",  # regular file
                    type="blob",
                    content=content,
                )
            )

        # 3. Create a new tree on top of the base
        new_tree = repo.create_git_tree(tree_elements, base_tree)

        # 4. Create the commit
        new_commit = repo.create_git_commit(
            message=commit_message,
            tree=new_tree,
            parents=[latest_commit],
        )

        # 5. Update the branch ref to point to the new commit
        ref.edit(sha=new_commit.sha)

        commit_url = f"https://github.com/{repo_full_name}/commit/{new_commit.sha}"
        logger.info("Committed %s to %s/%s", new_commit.sha[:8], repo_full_name, branch)

        return CommitResult(
            sha=new_commit.sha, url=commit_url, branch_created=branch_created
        )

    # ------------------------------------------------------------------
    # Optional: create a pull request
    # ------------------------------------------------------------------
    def create_pull_request(
        self,
        repo_full_name: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str = "",
    ) -> str:
        """Create a PR from *head_branch* into *base_branch*.

        Returns the PR HTML URL.  If a PR already exists for the same
        head/base pair the existing URL is returned instead.
        """
        repo = self._gh.get_repo(repo_full_name)
        try:
            pr = repo.create_pull(
                title=title,
                body=body,
                head=head_branch,
                base=base_branch,
            )
            logger.info("Created PR #%s on %s", pr.number, repo_full_name)
            return pr.html_url
        except GithubException as exc:
            # 422 usually means a PR already exists for this head→base
            if exc.status == 422:
                pulls = repo.get_pulls(
                    state="open", head=f"{repo.owner.login}:{head_branch}", base=base_branch
                )
                for existing in pulls:
                    logger.info("PR already exists: %s", existing.html_url)
                    return existing.html_url
            raise
