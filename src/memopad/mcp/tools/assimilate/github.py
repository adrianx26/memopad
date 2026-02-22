"""GitHub repository cloning and file extraction."""

import asyncio
import errno
import glob
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlparse

from loguru import logger

from .config import DEFAULT_CONFIG, REPO_FILE_PATTERNS
from .content_detector import detect_content_type
from .types import CrawlResult


def handle_remove_readonly(func, path, exc):
    """Error handler for shutil.rmtree to clean up read-only files."""
    excvalue = exc[1]
    if func in (os.rmdir, os.remove, os.unlink) and excvalue.errno == errno.EACCES:
        os.chmod(path, stat.S_IWRITE)
        func(path)


def is_github_repo(url: str) -> bool:
    """Check if URL is a GitHub repository."""
    parsed = urlparse(url)
    return "github.com" in parsed.netloc and len(parsed.path.strip("/").split("/")) >= 2


def _file_priority(fpath: str) -> int:
    """Determine priority for file sorting (lower = higher priority)."""
    name = os.path.basename(fpath).lower()
    if "readme" in name:
        return 0
    if "agent" in name or "claude" in name or "skill" in name:
        return 1
    if "doc" in fpath.lower():
        return 2
    return 3


async def clone_github_repo(url: str, max_files: int = 0) -> CrawlResult:
    """Clone a GitHub repository and extract relevant files.

    Returns:
        CrawlResult with pages, links, and errors.
    """
    empty_result: CrawlResult = {
        "pages": [],
        "all_github_links": [],
        "all_external_links": [],
        "errors": [],
    }

    temp_dir = tempfile.mkdtemp(prefix="memopad_git_")
    try:
        logger.info(f"assimilate: cloning {url} to {temp_dir}")

        # Disable interactive prompts (prevent hanging on private repos)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"

        try:
            if sys.platform == "win32":

                def run_git():
                    return subprocess.run(
                        ["git", "clone", "--depth", "1", url, temp_dir],
                        capture_output=True,
                        timeout=DEFAULT_CONFIG.git_timeout,
                        env=env,
                    )

                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, run_git)
                returncode = result.returncode
                stderr = result.stderr
            else:
                process = await asyncio.create_subprocess_exec(
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    url,
                    temp_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                _, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=DEFAULT_CONFIG.git_timeout
                )
                returncode = process.returncode
        except FileNotFoundError:
            error_msg = (
                "Git is not installed or not in PATH. "
                "Please install git to clone GitHub repositories."
            )
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result
        except (asyncio.TimeoutError, TimeoutError):
            error_msg = (
                f"Git clone timed out after 5 minutes for {url}. "
                "The repository may be too large."
            )
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result
        except asyncio.CancelledError:
            error_msg = f"Git clone was cancelled for {url}. Try pre-cloning the repo manually."
            logger.warning(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result
        except PermissionError as e:
            error_msg = f"Permission denied when cloning {url}: {e}"
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result
        except OSError as e:
            error_msg = f"OS error when cloning {url}: {type(e).__name__}: {e}"
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result

        if returncode != 0:
            stderr_text = stderr.decode() if stderr else "No error message"
            error_msg = f"Git clone failed for {url}: {stderr_text}"
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result

        # Walk and find interesting files
        logger.debug(f"assimilate: scanning for interesting files in {temp_dir}")
        pages: list[dict] = []

        found_files = []
        for pattern in REPO_FILE_PATTERNS:
            found_files.extend(
                glob.glob(os.path.join(temp_dir, pattern), recursive=True)
            )

        logger.info(f"assimilate: found {len(found_files)} potential files in repo")

        # Prioritize important files
        found_files.sort(key=_file_priority)

        # Limit processed files
        effective_max = max_files if max_files > 0 else DEFAULT_CONFIG.default_max_files
        found_files = found_files[:effective_max]

        for file_path in found_files:
            try:
                rel_path = os.path.relpath(file_path, temp_dir)
                # Skip .git directory
                if ".git" in rel_path.split(os.sep):
                    continue

                # Log large files but do not skip them
                try:
                    file_size = os.path.getsize(file_path)
                    if file_size > DEFAULT_CONFIG.large_file_threshold:
                        logger.info(
                            f"assimilate: reading large file ({file_size} bytes): {rel_path}"
                        )
                except OSError:
                    pass

                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(DEFAULT_CONFIG.max_file_read_size)

                if not content.strip():
                    continue

                # Construct a URL-like identifier for consistency
                file_url = f"{url.rstrip('/')}/{rel_path.replace(os.sep, '/')}"

                content_types = detect_content_type(file_url, content)

                # Links not extracted for GitHub files (simplified approach)
                links = {"internal": [], "github": [], "external": []}

                pages.append({
                    "url": file_url,
                    "text": content,
                    "content_types": content_types,
                    "links": links,
                    "is_file": True,
                })
            except Exception as e:
                logger.warning(f"Failed to read file {file_path}: {e}")

        return {
            "pages": pages,
            "all_github_links": [],  # Git clone approach doesn't extract links efficiently yet
            "all_external_links": [],
            "errors": [],
        }

    except Exception as e:
        error_msg = f"Unexpected error processing repository {url}: {type(e).__name__}: {e}"
        logger.exception(f"assimilate: {error_msg}")
        empty_result["errors"] = [error_msg]
        return empty_result
    finally:
        # Use robust error handler for Windows git files
        shutil.rmtree(temp_dir, onerror=handle_remove_readonly)
