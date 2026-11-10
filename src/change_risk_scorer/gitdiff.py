"""Read the list of changed files between two refs from a local git repository."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    deleted: int

    @property
    def lines(self) -> int:
        return self.added + self.deleted


def parse_numstat(output: str) -> list[FileChange]:
    """Parse `git diff --numstat` output. Binary files ('-' counts) count as 0 lines."""
    files = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        files.append(FileChange(path, int(added) if added.isdigit() else 0,
                                int(deleted) if deleted.isdigit() else 0))
    return files


def changed_files(repo: str, base: str, head: str) -> list[FileChange]:
    """Files changed on `head` since it diverged from `base` (merge-base diff)."""
    cmd = ["git", "-C", repo, "-c", "core.quotepath=off", "diff", "--numstat",
           "--no-renames", f"{base}...{head}", "--"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on PATH") from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "git diff failed"
        raise GitError(
            f"cannot diff {base}...{head} in {repo}: {detail} "
            "(check both refs exist locally; shallow clones need full history)"
        )
    return parse_numstat(proc.stdout)
