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


class GitHubService:
    """High-level helper that wraps PyGithub for our specific use-case."""

    def __init__(self, settings: Settings) -> None:
        auth = Auth.Token(settings.github_token)
        self._gh = Github(auth=auth)

    # ------------------------------------------------------------------
    # Core: commit a set of files onto an existing branch
    # ------------------------------------------------------------------
    def commit_files(
        self,
        repo_full_name: str,
        branch: str,
        files: dict[str, str],
        commit_message: str,
    ) -> CommitResult:
        """Create a single commit that writes *files* to *branch*.

        Parameters
        ----------
        repo_full_name:
            ``"owner/repo"`` identifier.
        branch:
            Target branch name (must already exist).
        files:
            ``{file_path: file_content}`` mapping.
        commit_message:
            Commit message string.

        Returns
        -------
        CommitResult with the new commit SHA and URL.
        """
        repo = self._gh.get_repo(repo_full_name)

        # 1. Get the SHA of the latest commit on the branch
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

        return CommitResult(sha=new_commit.sha, url=commit_url)

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
