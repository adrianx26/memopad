"""Assimilate tool for Memopad MCP server.

Crawls a URL, extracts knowledge (content, links, agent profiles, skills, rules),
and stores everything as structured notes in memopad.
"""

import asyncio
import os
import re
import shutil
import tempfile
import glob
import stat
import errno
from html.parser import HTMLParser

def handle_remove_readonly(func, path, exc):
    """Error handler for shutil.rmtree to clean up read-only files (common with git)."""
    excvalue = exc[1]
    if func in (os.rmdir, os.remove, os.unlink) and excvalue.errno == errno.EACCES:
        os.chmod(path, stat.S_IWRITE)
        func(path)
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
import io
import csv
import webbrowser
from PIL import Image
try:
    import pypdf
except ImportError:
    pypdf = None
try:
    import docx
except ImportError:
    docx = None
try:
    import openpyxl
except ImportError:
    openpyxl = None
from fastmcp import Context
from loguru import logger

from memopad.mcp.async_client import get_client
from memopad.mcp.project_context import get_active_project, add_project_metadata
from memopad.mcp.server import mcp
from memopad.schemas.base import Entity


# ---------------------------------------------------------------------------
# HTML helpers (stdlib only)
# ---------------------------------------------------------------------------

class LinkExtractor(HTMLParser):
    """Extract all href links from HTML."""

    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for attr, value in attrs:
                if attr == "href" and value:
                    self.links.append(value)


class HTMLToText(HTMLParser):
    """Convert HTML to plain readable text (lightweight, no deps)."""

    SKIP_TAGS = frozenset([
        "script", "style", "noscript", "svg", "head", "nav", "footer",
        "template", "iframe",
    ])
    BLOCK_TAGS = frozenset([
        "p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "blockquote", "pre", "section", "article",
        "header", "main", "aside", "details", "summary", "figcaption",
    ])
    HEADING_TAGS = frozenset(["h1", "h2", "h3", "h4", "h5", "h6"])

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.HEADING_TAGS:
            level = int(tag[1])
            self._pieces.append("\n" + "#" * level + " ")
        elif tag in self.BLOCK_TAGS:
            self._pieces.append("\n")
        elif tag == "br":
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS or tag in self.HEADING_TAGS:
            self._pieces.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        # Collapse excessive blank lines
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def extract_links(html: str, base_url: str) -> list[str]:
    """Extract and resolve all links from HTML."""
    parser = LinkExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    resolved = []
    for href in parser.links:
        # Skip anchors, javascript, mailto
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urljoin(base_url, href)
        # Strip fragment
        full = full.split("#")[0]
        if full:
            resolved.append(full)
    return resolved


def html_to_text(html: str) -> str:
    """Convert HTML to readable plain text."""
    parser = HTMLToText()
    try:
        parser.feed(html)
    except Exception:
        return ""
    return parser.get_text()


def categorize_links(links: list[str], base_domain: str) -> dict[str, list[str]]:
    """Categorize links into internal, github, and external."""
    internal: list[str] = []
    github: list[str] = []
    external: list[str] = []
    seen: set[str] = set()

    for link in links:
        if link in seen:
            continue
        seen.add(link)
        parsed = urlparse(link)
        domain = parsed.netloc.lower()

        if "github.com" in domain or "raw.githubusercontent.com" in domain:
            github.append(link)
        elif domain == base_domain or domain.endswith("." + base_domain):
            internal.append(link)
        else:
            external.append(link)

    return {"internal": internal, "github": github, "external": external}


# ---------------------------------------------------------------------------
# Content detection helpers
# ---------------------------------------------------------------------------

AGENT_PROFILE_PATTERNS = re.compile(
    r"(AGENTS\.md|CLAUDE\.md|SYSTEM_PROMPT|system[_-]?prompt|agent[_-]?profile|"
    r"\.cursorrules|cursor[_-]?rules|copilot[_-]?instructions|\.github/copilot)",
    re.IGNORECASE,
)

SKILLS_PATTERNS = re.compile(
    r"(SKILL\.md|skills/|\.agent/|workflows/|\.gemini/|"
    r"rules[_-]?file|RULES\.md|\.clinerules|\.windsurfrules)",
    re.IGNORECASE,
)

CONFIG_PATTERNS = re.compile(
    r"(README\.md|readme\.md|pyproject\.toml|package\.json|Cargo\.toml|"
    r"setup\.py|setup\.cfg|Makefile|justfile|docker-compose|Dockerfile)",
    re.IGNORECASE,
)

SOUL_PATTERNS = re.compile(
    r"(soul\.md|SOUL\.md|identity\.md|persona|personality|core[_-]?values|"
    r"purpose\.md|mission|manifesto|beliefs|principles|ethos|"
    r"character\.md|worldview|philosophy\.md|creed)",
    re.IGNORECASE,
)

TOOLS_FUNCTIONS_PATTERNS = re.compile(
    r"(@tool|@mcp\.tool|def\s+tool_|function[_-]?calling|api[_-]?endpoint|"
    r"register[_-]?tool|tool[_-]?schema|openapi|swagger|handler|"
    r"@app\.(get|post|put|delete|patch)|@router\.|def\s+handle_|"
    r"tools/|functions/|endpoints/|api/)",
    re.IGNORECASE,
)

ALGORITHMS_PATTERNS = re.compile(
    r"(algorithm|sorting|binary[_-]?search|traversal|dynamic[_-]?programming|"
    r"recursion|backtracking|greedy|dijkstra|breadth[_-]?first|depth[_-]?first|"
    r"hashing|optimization|time[_-]?complexity|space[_-]?complexity|"
    r"O\(n|O\(log|divide[_-]?and[_-]?conquer|memoization)",
    re.IGNORECASE,
)

DECISION_PATTERNS = re.compile(
    r"(decision[_-]?tree|state[_-]?machine|fsm|finite[_-]?state|"
    r"workflow[_-]?engine|branching[_-]?logic|conditional[_-]?flow|"
    r"routing[_-]?logic|dispatch|strategy[_-]?pattern|policy[_-]?engine|"
    r"rule[_-]?engine|control[_-]?flow|switch[_-]?case|decision[_-]?table)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Safety limits for content processing
# ---------------------------------------------------------------------------

# Max bytes to read per file when cloning a repo (1GB)
MAX_FILE_READ_SIZE = 1_000_000_000

# Default cap on files to process from a repo (when max_files=0/unlimited)
DEFAULT_MAX_FILES = 2_000

# Safety margin below Entity MAX_CONTENT_LENGTH (50M) for note content
MAX_NOTE_CONTENT = 49_000_000


def _safe_truncate(content: str | None, max_len: int = MAX_NOTE_CONTENT) -> str | None:
    """Truncate content to max_len with a marker if it exceeds the limit."""
    if content and len(content) > max_len:
        return content[:max_len] + "\n\n[... content truncated to fit size limit ...]"
    return content


# ---------------------------------------------------------------------------
# File Processing Helpers
# ---------------------------------------------------------------------------

class FileProcessor:
    """Helper to extract text/metadata from various file formats."""

    @staticmethod
    def extract_pdf_text(content: bytes) -> str:
        if not pypdf:
            return "Error: pypdf not installed."
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            text = []
            for page in reader.pages:
                text.append(page.extract_text() or "")
            return "\n".join(text)
        except Exception as e:
            logger.error(f"Error extracting PDF: {e}")
            return f"Error extracting PDF: {e}"

    @staticmethod
    def extract_docx_text(content: bytes) -> str:
        if not docx:
            return "Error: python-docx not installed."
        try:
            doc = docx.Document(io.BytesIO(content))
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            logger.error(f"Error extracting DOCX: {e}")
            return f"Error extracting DOCX: {e}"

    @staticmethod
    def extract_xlsx_text(content: bytes) -> str:
        if not openpyxl:
            return "Error: openpyxl not installed."
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            text = []
            for sheet in wb.worksheets:
                text.append(f"Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    row_text = [str(cell) for cell in row if cell is not None]
                    if row_text:
                        text.append(" | ".join(row_text))
                text.append("\n")
            return "\n".join(text)
        except Exception as e:
            logger.error(f"Error extracting XLSX: {e}")
            return f"Error extracting XLSX: {e}"

    @staticmethod
    def extract_image_info(content: bytes) -> str:
        try:
            img = Image.open(io.BytesIO(content))
            info = [
                f"Format: {img.format}",
                f"Size: {img.size} (Width x Height)",
                f"Mode: {img.mode}",
                f"Info: {img.info}",
            ]
            
            # OCR Capability (Commented out as requested)
            # if pytesseract:
            #     text = pytesseract.image_to_string(img)
            #     info.append("\n--- OCR Detected Text ---\n")
            #     info.append(text)
            
            return "\n".join(info)
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            return f"Error processing image: {e}"

    @staticmethod
    def extract_text_content(content: bytes, content_type: str, url: str) -> str:
        """Dispatch to appropriate extractor based on content type/extension."""
        url_lower = url.lower()
        
        # PDF
        if "application/pdf" in content_type or url_lower.endswith(".pdf"):
            return FileProcessor.extract_pdf_text(content)
            
        # Word
        if "wordprocessingml" in content_type or url_lower.endswith(".docx") or url_lower.endswith(".doc"):
             # .doc is binary, python-docx only supports .docx. .doc support requires other tools or is limited.
             if url_lower.endswith(".doc"):
                 return "Error: .doc format not supported directly (only .docx)"
             return FileProcessor.extract_docx_text(content)
             
        # Excel
        if "spreadsheet" in content_type or "excel" in content_type or url_lower.endswith(".xlsx") or url_lower.endswith(".xls"):
            if url_lower.endswith(".xls"):
                 return "Error: .xls format not supported directly (only .xlsx)"
            return FileProcessor.extract_xlsx_text(content)
            
        # CSV
        if "text/csv" in content_type or url_lower.endswith(".csv"):
             try:
                 text_content = content.decode("utf-8", errors="replace")
                 # Check if it parses as CSV
                 # csv.reader expects a text stream
                 return text_content
             except Exception:
                 return "Error reading CSV"
                 
        # Image
        if "image/" in content_type or url_lower.endswith((".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")):
            return FileProcessor.extract_image_info(content)
            
        # Text/Code
        # Try to decode as text
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return "Binary content (decoding failed)"


def detect_content_type(url: str, text: str) -> list[str]:
    """Identify what kind of useful content a page contains."""
    types: list[str] = []
    combined = url + "\n" + text[:3000]

    if AGENT_PROFILE_PATTERNS.search(combined):
        types.append("agent_profile")
    if SKILLS_PATTERNS.search(combined):
        types.append("skills_rules")
    if CONFIG_PATTERNS.search(combined):
        types.append("config_docs")
    if SOUL_PATTERNS.search(combined):
        types.append("soul_file")
    if TOOLS_FUNCTIONS_PATTERNS.search(combined):
        types.append("tools_functions")
    if ALGORITHMS_PATTERNS.search(combined):
        types.append("algorithms")
    if DECISION_PATTERNS.search(combined):
        types.append("decision_structure")

    # Detect conceptual content
    concept_keywords = [
        "architecture", "design pattern", "framework", "plugin system",
        "extension", "middleware", "pipeline", "workflow engine",
        "knowledge graph", "semantic", "embedding", "vector",
        "rag", "retrieval", "agent", "tool use", "function calling",
    ]
    text_lower = text[:5000].lower()
    if sum(1 for kw in concept_keywords if kw in text_lower) >= 2:
        types.append("concepts")

    return types


# ---------------------------------------------------------------------------
# GitHub Cloning Logic
# ---------------------------------------------------------------------------

def _is_github_repo(url: str) -> bool:
    """Check if URL is a GitHub repository."""
    parsed = urlparse(url)
    return "github.com" in parsed.netloc and len(parsed.path.strip("/").split("/")) >= 2

async def _clone_github_repo(url: str, max_files: int = 0) -> dict:
    """Clone a GitHub repository and extract relevant files.
    
    Raises:
        No exceptions - all errors are caught and returned in the errors list.
    """
    # Default empty result structure
    empty_result = {
        "pages": [],
        "all_github_links": [],
        "all_external_links": [],
        "errors": []
    }
    
    # 1. Clone
    temp_dir = tempfile.mkdtemp(prefix="memopad_git_")
    try:
        logger.info(f"Cloning {url} to {temp_dir}")
        
        # --- Git subprocess execution with comprehensive error handling ---
        # Trigger: User attempts to assimilate a GitHub repository
        # Why: Git may not be installed, clone may fail, or operation may timeout
        # Outcome: Return descriptive error message instead of crashing MCP tool
        try:
            # Disable interactive prompts (prevent hanging on private repos)
            env = os.environ.copy()
            env["GIT_TERMINAL_PROMPT"] = "0"
            
            process = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", url, temp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            # 5 minute timeout for large repositories
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=300.0)
        except FileNotFoundError:
            error_msg = "Git is not installed or not in PATH. Please install git to clone GitHub repositories."
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result
        except asyncio.TimeoutError:
            error_msg = f"Git clone timed out after 5 minutes for {url}. The repository may be too large."
            logger.error(f"assimilate: {error_msg}")
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
        
        if process.returncode != 0:
            stderr_text = stderr.decode() if stderr else "No error message"
            error_msg = f"Git clone failed for {url}: {stderr_text}"
            logger.error(f"assimilate: {error_msg}")
            empty_result["errors"] = [error_msg]
            return empty_result

        # 2. Walk and find interesting files
        pages = []
        file_patterns = [
            "**/*.md", "**/*.txt", "**/*.rst", 
            "**/COPYING", "**/LICENSE", "**/NOTICE",
            "**/*.py", "**/*.js", "**/*.ts", "**/*.go", "**/*.rs", "**/*.java", "**/*.c", "**/*.cpp", "**/*.h", "**/*.hpp",
            "**/*.json", "**/*.toml", "**/*.yaml", "**/*.yml"
        ]
        
        found_files = []
        for pattern in file_patterns:
             found_files.extend(glob.glob(os.path.join(temp_dir, pattern), recursive=True))

        # Prioritize important files
        def priority(fpath):
            name = os.path.basename(fpath).lower()
            if "readme" in name:
                return 0
            if "agent" in name or "claude" in name or "skill" in name:
                return 1
            if "doc" in fpath.lower():
                return 2
            return 3
            
        found_files.sort(key=priority)
        
        # Limit processed files — always cap even when max_files=0 (unlimited)
        effective_max = max_files if max_files > 0 else DEFAULT_MAX_FILES
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
                    if file_size > 10_000_000:  # 10MB
                        logger.info(f"assimilate: reading large file ({file_size} bytes): {rel_path}")
                except OSError:
                    pass
                    
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read(MAX_FILE_READ_SIZE)
                    
                if not content.strip():
                    continue

                # Construct a URL-like identifier for consistency
                # e.g. https://github.com/owner/repo/blob/main/README.md (approximate)
                file_url = f"{url.rstrip('/')}/{rel_path.replace(os.sep, '/')}"
                
                content_types = detect_content_type(file_url, content)
                
                # Check for links (basic regex for now as we don't parse markdown fully here)
                # This is a simplification compared to HTML parsing
                links = {"internal": [], "github": [], "external": []} 
                
                pages.append({
                    "url": file_url,
                    "text": content,
                    "content_types": content_types,
                    "links": links,
                    "is_file": True # Marker for file-based content
                })
            except Exception as e:
                logger.warning(f"Failed to read file {file_path}: {e}")

        return {
            "pages": pages,
            "all_github_links": [],  # Git clone approach doesn't extract links efficiently yet
            "all_external_links": [],
            "errors": []
        }

    except Exception as e:
        # Catch-all for any unexpected errors during file processing
        error_msg = f"Unexpected error processing repository {url}: {type(e).__name__}: {e}"
        logger.exception(f"assimilate: {error_msg}")
        empty_result["errors"] = [error_msg]
        return empty_result
    finally:
        # Use robust error handler for Windows git files
        shutil.rmtree(temp_dir, onerror=handle_remove_readonly)

# ---------------------------------------------------------------------------
# Crawler
# ---------------------------------------------------------------------------

async def _fetch_page(
    http_client: httpx.AsyncClient, url: str
) -> tuple[str, str] | None:
    """Fetch a single page. Returns (html, final_url) or None on failure."""
    try:
        resp = await http_client.get(
            url,
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        )
        if resp.status_code != 200:
            logger.debug(f"assimilate: HTTP {resp.status_code} for {url}")
            return None
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            # Skip binary files, images, etc.
            logger.debug(f"assimilate: skipping non-text content at {url}")
            return None
        return resp.text, str(resp.url)
    except Exception as e:
        logger.debug(f"assimilate: failed to fetch {url}: {e}")
        return None


async def crawl(
    start_url: str, max_depth: int = 10, max_pages: int = 0
) -> dict:
    """Crawl starting from a URL, returning structured results.

    Returns dict with keys:
      - pages: list of {url, text, content_types, links}
      - all_github_links: deduplicated list of GitHub URLs
      - all_external_links: deduplicated list of external URLs
      - errors: list of URLs that failed
    """
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc.lower()

    visited: set[str] = set()
    pages: list[dict] = []
    all_github: set[str] = set()
    all_external: set[str] = set()
    errors: list[str] = []

    queue: list[tuple[str, int]] = [(start_url, 0)]

    headers = {
        "User-Agent": "Memopad-Assimilate/1.0 (knowledge-crawler)",
        "Accept": "text/html,text/plain,application/xhtml+xml",
    }

    async with httpx.AsyncClient(headers=headers) as http_client:
        while queue and (max_pages == 0 or len(pages) < max_pages):
            url, depth = queue.pop(0)

            # Normalize trailing slash
            normalized = url.rstrip("/")
            if normalized in visited or url in visited:
                continue
            visited.add(url)
            visited.add(normalized)

            result = await _fetch_page(http_client, url)
            if result is None:
                errors.append(url)
                continue

            html, final_url = result
            
            # If this is the start URL, update base_domain based on where we landed
            # to handle redirects (e.g. http -> https, non-www -> www)
            if depth == 0:
                parsed_final = urlparse(final_url)
                new_base = parsed_final.netloc.lower()
                if new_base.startswith("www."):
                    new_base = new_base[4:]
                base_domain = new_base

            text = html_to_text(html)
            links = extract_links(html, final_url)
            categorized = categorize_links(links, base_domain)

            content_types = detect_content_type(url, text)

            pages.append({
                "url": final_url,
                "text": text,
                "content_types": content_types,
                "links": categorized,
                "is_file": False
            })

            all_github.update(categorized["github"])
            all_external.update(categorized["external"])

            # Queue internal links for deeper crawling
            if depth < max_depth:
                for internal_link in categorized["internal"]:
                    if internal_link not in visited and internal_link.rstrip("/") not in visited:
                        queue.append((internal_link, depth + 1))

            # Also queue GitHub links (they often have READMEs with useful info)
            if depth < max_depth:
                for gh_link in categorized["github"]:
                    if gh_link not in visited and gh_link.rstrip("/") not in visited:
                        queue.append((gh_link, depth + 1))

            # Rate limit
            await asyncio.sleep(0.5)

    return {
        "pages": pages,
        "all_github_links": sorted(all_github),
        "all_external_links": sorted(all_external),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Note builders
# ---------------------------------------------------------------------------

def _build_overview_note(start_url: str, data: dict) -> str:
    """Build the main overview note content."""
    lines = [
        f"# Assimilated: {start_url}\n",
        f"- [source] Source URL: {start_url}",
        f"- [stats] Pages processed: {len(data['pages'])}",
        f"- [stats] GitHub links found: {len(data['all_github_links'])}",
        f"- [stats] External links found: {len(data['all_external_links'])}",
        f"- [stats] Errors: {len(data['errors'])}",
        "",
        "## Pages/Files Processed\n",
    ]
    for page in data["pages"]:
        types_str = ", ".join(page["content_types"]) if page["content_types"] else "general"
        lines.append(f"- [{types_str}] {page['url']}")

    if data["errors"]:
        lines.append("\n## Errors\n")
        for err in data["errors"]:
            lines.append(f"- {err}")

    # Add a summary of the first page content (usually the main README)
    if data["pages"]:
        # Find README if possible
        readme = next((p for p in data["pages"] if "readme" in p["url"].lower()), data["pages"][0])
        first_text = readme["text"][:2000]
        lines.append(f"\n## Main Content Summary ({readme['url']})\n")
        lines.append(first_text)

    return _safe_truncate("\n".join(lines))


def _build_agent_profiles_note(data: dict) -> str | None:
    """Build note for discovered agent profiles and system prompts."""
    sections = []
    for page in data["pages"]:
        if "agent_profile" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            # Take meaningful portion of text
            sections.append(page["text"][:5000]) # Increased limit for profiles
            sections.append("")

    if not sections:
        return None

    header = "# Agent Profiles & System Prompts\n\n"
    header += "- [category] Extracted agent profiles, system prompts, and AI instructions\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_skills_rules_note(data: dict) -> str | None:
    """Build note for discovered skills, rules, and workflows."""
    sections = []
    for page in data["pages"]:
        if "skills_rules" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:5000]) # Increased limit for skills
            sections.append("")

    if not sections:
        return None

    header = "# Skills, Rules & Workflows\n\n"
    header += "- [category] Extracted skills definitions, rules files, and workflow patterns\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_concepts_note(data: dict) -> str | None:
    """Build note for discovered concepts and ideas."""
    sections = []
    for page in data["pages"]:
        if "concepts" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:3000])
            sections.append("")

    if not sections:
        return None

    header = "# Concepts & Ideas\n\n"
    header += "- [category] Extracted architectural concepts, design patterns, and ideas\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_soul_files_note(data: dict) -> str | None:
    """Build note for discovered soul/identity files."""
    sections = []
    for page in data["pages"]:
        if "soul_file" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:5000])
            sections.append("")

    if not sections:
        return None

    header = "# Soul Files & Identity\n\n"
    header += "- [category] Extracted soul files, identity definitions, personality, values, and purpose statements\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_tools_functions_note(data: dict) -> str | None:
    """Build note for discovered tools, functions, and API definitions."""
    sections = []
    for page in data["pages"]:
        if "tools_functions" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:5000])
            sections.append("")

    if not sections:
        return None

    header = "# Tools & Functions\n\n"
    header += "- [category] Extracted tool definitions, function registrations, API endpoints, and handlers\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_algorithms_note(data: dict) -> str | None:
    """Build note for discovered algorithm implementations."""
    sections = []
    for page in data["pages"]:
        if "algorithms" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:5000])
            sections.append("")

    if not sections:
        return None

    header = "# Algorithms & Implementations\n\n"
    header += "- [category] Extracted algorithm implementations, data structures, and computational logic\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_decision_structure_note(data: dict) -> str | None:
    """Build note for discovered decision structures and state machines."""
    sections = []
    for page in data["pages"]:
        if "decision_structure" in page.get("content_types", []):
            sections.append(f"## From: {page['url']}\n")
            sections.append(page["text"][:5000])
            sections.append("")

    if not sections:
        return None

    header = "# Decision Structures\n\n"
    header += "- [category] Extracted decision trees, state machines, routing logic, and control flow patterns\n\n"
    return _safe_truncate(header + "\n".join(sections))


def _build_functional_diagram_note(data: dict) -> str | None:
    """Build a Mermaid flowchart diagram of the assimilated content structure.

    Analyzes all pages/files to create a schematic showing:
    - Components grouped by content type
    - Relationships between files based on directory structure
    - Color-coded nodes by category
    """
    if not data["pages"]:
        return None

    # Color map for content types
    type_colors = {
        "config_docs": "#4CAF50",      # green
        "agent_profile": "#9C27B0",    # purple
        "skills_rules": "#FF9800",     # orange
        "concepts": "#2196F3",         # blue
        "soul_file": "#E91E63",        # pink
        "tools_functions": "#00BCD4",  # cyan
        "algorithms": "#FF5722",       # deep orange
        "decision_structure": "#795548",  # brown
    }

    nodes = []
    edges = []
    styles = []
    seen_ids = set()

    # Build a node for each page
    for i, page in enumerate(data["pages"]):
        url = page["url"]
        # Create a readable short label from the URL
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        label = path.split("/")[-1] if path else parsed.netloc
        if not label:
            label = url[:40]
        # Sanitize label for Mermaid (remove special chars)
        label = re.sub(r'["\'\[\]{}()<>|&;]', '', label)
        if len(label) > 40:
            label = label[:37] + "..."

        node_id = f"N{i}"
        seen_ids.add(node_id)

        types_str = ", ".join(page.get("content_types", [])) or "general"
        nodes.append(f'    {node_id}["{label}\\n({types_str})"]')

        # Style by primary content type
        ctypes = page.get("content_types", [])
        if ctypes:
            primary_type = ctypes[0]
            if primary_type in type_colors:
                styles.append(f"    style {node_id} fill:{type_colors[primary_type]},color:#fff")

    # Build edges: connect files in the same directory hierarchy
    # Group pages by directory prefix
    dir_groups: dict[str, list[int]] = {}
    for i, page in enumerate(data["pages"]):
        parsed = urlparse(page["url"])
        parts = parsed.path.strip("/").split("/")
        if len(parts) > 1:
            parent_dir = "/".join(parts[:-1])
        else:
            parent_dir = "root"
        dir_groups.setdefault(parent_dir, []).append(i)

    # Connect first node in each group to other nodes in same group
    for dir_name, indices in dir_groups.items():
        if len(indices) > 1:
            root_idx = indices[0]
            for child_idx in indices[1:]:
                edges.append(f"    N{root_idx} --> N{child_idx}")

    # Also connect root-level items to first sub-items
    root_indices = dir_groups.get("root", [])
    other_roots = [idx_list[0] for key, idx_list in dir_groups.items()
                   if key != "root" and idx_list]
    if root_indices and other_roots:
        for sub_root in other_roots[:10]:  # Limit connections to avoid clutter
            edges.append(f"    N{root_indices[0]} --> N{sub_root}")

    # Build the Mermaid block
    mermaid_lines = ["graph TD"]
    mermaid_lines.extend(nodes[:50])  # Cap at 50 nodes for readability
    mermaid_lines.extend(edges[:80])  # Cap edges
    mermaid_lines.extend(styles[:50])

    mermaid_block = "\n".join(mermaid_lines)

    # Build legend
    legend_lines = ["\n## Legend\n"]
    for ctype, color in type_colors.items():
        legend_lines.append(f"- 🟢 **{ctype}**: `{color}`")

    lines = [
        "# Functional Diagram\n",
        "- [category] Auto-generated schematic of assimilated content structure\n",
        f"- [stats] Total components: {len(data['pages'])}",
        f"- [stats] Content categories: {len(set(t for p in data['pages'] for t in p.get('content_types', [])))}\n",
        "```mermaid",
        mermaid_block,
        "```",
    ]
    lines.extend(legend_lines)

    return _safe_truncate("\n".join(lines))


def _build_github_links_note(data: dict) -> str | None:
    """Build the GitHub links index note."""
    if not data["all_github_links"]:
        return None

    lines = [
        "# GitHub Links Index\n",
        "- [category] All GitHub links discovered during assimilation\n",
    ]
    for link in data["all_github_links"]:
        lines.append(f"- {link}")

    return _safe_truncate("\n".join(lines))





# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------

@mcp.tool(
    description="""Assimilate knowledge from a URL into memopad.

    Crawls the given URL (and linked pages), extracts useful content, and stores
    structured notes in the knowledge base. Automatically detects:
    - Agent profiles & system prompts
    - Skills, rules, and workflow definitions
    - Architectural concepts and design patterns
    - GitHub repository links
    - Documents (PDF, DOCX, XLSX) and Images

    Notes are stored under <domain>/ in the target project.

    Args:
        url: The URL to assimilate (e.g. "https://github.com/org/repo")
        project: Project to store notes in. Optional - uses default if not specified.

        max_depth: How many link-hops deep to crawl (default: 10)
        max_pages: Maximum pages to fetch (default: 0 = unlimited)
        open_browser: Open the URL in the system browser for visualization (default: False)
    """,
)
async def assimilate(
    url: str,
    project: Optional[str] = None,
    max_depth: int = 10,
    max_pages: int = 0,
    open_browser: bool = False,
    context: Context | None = None,
) -> str:
    """Assimilate knowledge from a URL into memopad.

    Crawls the URL and linked pages, extracts agent profiles, skills, rules,
    concepts, and GitHub links, then stores everything as structured notes.

    Project Resolution:
    Server resolves projects in this order: Single Project Mode -> project parameter -> default project.
    If project unknown, use list_memory_projects() or recent_activity() first.

    Args:
        url: The starting URL to crawl and assimilate
        project: Project name to store notes in. Optional.
        max_depth: Maximum crawl depth from start URL (default: 10)
        max_pages: Maximum total pages to crawl (default: 0 = unlimited)
        open_browser: Open the URL in the system browser for visualization (default: False)
        context: Optional FastMCP context for performance caching.

    Returns:
        Summary of what was crawled and stored.

    Examples:
        assimilate("https://github.com/org/repo")
        assimilate("https://github.com/org/repo", project="research", max_depth=3)

    Raises:
        ValueError: If URL is invalid or project doesn't exist
    """
    logger.info(f"MCP tool call tool=assimilate url={url} max_depth={max_depth} max_pages={max_pages} open_browser={open_browser}")

    # --- Top-level exception handling ---
    try:
        # Validate URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return "# Error\n\nInvalid URL. Please provide a full URL like https://example.com"

        domain = parsed.netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]

        # Open in browser if requested
        if open_browser:
            try:
                logger.info(f"Opening browser for {url}")
                webbrowser.open(url)
            except Exception as e:
                logger.error(f"Failed to open browser for {url}: {e}")

        data = None
        
        # Strategy 1: GitHub Repo
        if _is_github_repo(url):
            logger.info(f"assimilate: detected GitHub repo, cloning {url}")
            data = await _clone_github_repo(url, max_files=max_pages)
            path_parts = parsed.path.strip("/").split("/")
            if len(path_parts) >= 2:
                domain = f"{domain}/{path_parts[0]}/{path_parts[1]}"
        
        # Strategy 2: Check for direct file download
        else:
            # Check extension first
            is_file_ext = url.lower().endswith(
                (".pdf", ".docx", ".xlsx", ".csv", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp", ".txt", ".ini", ".py")
            )
            
            should_download_directly = is_file_ext
            content_type = ""
            
            # If not obvious extension, check Content-Type via HEAD request
            if not should_download_directly:
                try:
                    async with httpx.AsyncClient() as client:
                         head_resp = await client.head(url, follow_redirects=True, timeout=5.0)
                         content_type = head_resp.headers.get("content-type", "").lower()
                         if any(t in content_type for t in ["application/pdf", "wordprocessingml", "spreadsheet", "image/"]):
                             should_download_directly = True
                except Exception:
                    pass # Ignore HEAD errors, fall back to crawl

            if should_download_directly:
                 logger.info(f"assimilate: detected direct file download for {url}")
                 try:
                     async with httpx.AsyncClient() as client:
                         resp = await client.get(url, follow_redirects=True, timeout=30.0)
                         resp.raise_for_status()
                         content = resp.content
                         if not content_type:
                             content_type = resp.headers.get("content-type", "").lower()
                         
                         text = FileProcessor.extract_text_content(content, content_type, url)
                         
                         # Construct a single-page result
                         data = {
                            "pages": [{
                                "url": str(resp.url),
                                "text": text,
                                "content_types": ["file_content"],
                                "links": {"internal": [], "github": [], "external": []},
                                "is_file": True
                            }],
                            "all_github_links": [],
                            "all_external_links": [],
                            "errors": []
                         }
                 except Exception as e:
                     logger.error(f"Failed to download file {url}: {e}")
                     # Fallback to crawl if download fails? No, probably report error.
                     data = {"pages": [], "all_github_links": [], "all_external_links": [], "errors": [str(e)]}

        # Strategy 3: Generic Crawl (Fallback)
        if data is None:
            logger.info(f"assimilate: starting generic crawl of {url}")
            data = await crawl(url, max_depth=max_depth, max_pages=max_pages)

        logger.info(
            f"assimilate: processing complete — {len(data['pages'])} pages/files, "
            f"{len(data['all_github_links'])} github links"
        )

        if not data["pages"]:
            if data.get("errors"):
                error_details = "\n".join(f"- {e}" for e in data["errors"])
                return f"# Error\n\nCould not fetch content from {url}:\n\n{error_details}"
            return f"# Error\n\nCould not fetch any content from {url}"

        # Build notes
        notes_to_write: list[tuple[str, str]] = []

        overview = _build_overview_note(url, data)
        notes_to_write.append(("Overview", overview))

        agent_note = _build_agent_profiles_note(data)
        if agent_note:
            notes_to_write.append(("Agent Profiles", agent_note))

        skills_note = _build_skills_rules_note(data)
        if skills_note:
            notes_to_write.append(("Skills and Rules", skills_note))

        concepts_note = _build_concepts_note(data)
        if concepts_note:
            notes_to_write.append(("Concepts and Ideas", concepts_note))

        soul_note = _build_soul_files_note(data)
        if soul_note:
            notes_to_write.append(("Soul Files", soul_note))

        tools_note = _build_tools_functions_note(data)
        if tools_note:
            notes_to_write.append(("Tools and Functions", tools_note))

        algo_note = _build_algorithms_note(data)
        if algo_note:
            notes_to_write.append(("Algorithms", algo_note))

        decision_note = _build_decision_structure_note(data)
        if decision_note:
            notes_to_write.append(("Decision Structures", decision_note))

        diagram_note = _build_functional_diagram_note(data)
        if diagram_note:
            notes_to_write.append(("Functional Diagram", diagram_note))

        github_note = _build_github_links_note(data)
        if github_note:
            notes_to_write.append(("GitHub Links Index", github_note))

        # Store notes in memopad
        # explicitly controlled directory path
        directory = domain  # No 'Assimilated/' prefix!

        async with get_client() as client:
            active_project = await get_active_project(client, project, context)

            from memopad.mcp.clients import KnowledgeClient
            knowledge_client = KnowledgeClient(client, active_project.external_id)

            stored: list[str] = []
            for title, content in notes_to_write:
                try:
                    # Truncate content as defense-in-depth before Entity validation
                    safe_content = _safe_truncate(content)
                    entity = Entity(
                        title=title,
                        directory=directory,
                        entity_type="note",
                        content_type="text/markdown",
                        content=safe_content,
                        entity_metadata={"tags": ["assimilated", domain]},
                    )
                    try:
                        result = await knowledge_client.create_entity(entity.model_dump(), fast=False)
                    except Exception as e:
                        if "409" in str(e) or "conflict" in str(e).lower() or "already exists" in str(e).lower():
                            if entity.permalink:
                                try:
                                    entity_id = await knowledge_client.resolve_entity(entity.permalink)
                                    result = await knowledge_client.update_entity(
                                        entity_id, entity.model_dump(), fast=False
                                    )
                                    logger.info(f"assimilate: updated existing note '{title}' at {result.permalink}")
                                except Exception as update_err:
                                    logger.error(f"assimilate: update failed for '{title}': {update_err}")
                                    raise update_err
                            else:
                                raise
                        else:
                            raise
                    stored.append(f"- {title}: {result.permalink}")
                    logger.info(f"assimilate: stored note '{title}' at {result.permalink}")
                except Exception as e:
                    stored.append(f"- {title}: FAILED ({e})")
                    logger.error(f"assimilate: failed to store note '{title}': {e}")

        # Build summary
        summary_lines = [
            "# Assimilation Complete\n",
            f"source: {url}",
            f"project: {active_project.name}",
            f"items_processed: {len(data['pages'])}",
            f"github_links_found: {len(data['all_github_links'])}",
            f"notes_stored: {len(notes_to_write)}",
            f"directory: {directory}",
            "\n## Notes Created\n",
        ]
        summary_lines.extend(stored)

        if data["all_github_links"]:
            summary_lines.append(f"\n## GitHub Links ({len(data['all_github_links'])})\n")
            for gh in data["all_github_links"][:20]:
                summary_lines.append(f"- {gh}")
            if len(data["all_github_links"]) > 20:
                summary_lines.append(f"- ... and {len(data['all_github_links']) - 20} more (see GitHub Links Index note)")

        summary_result = "\n".join(summary_lines)
        return add_project_metadata(summary_result, active_project.name)

    except Exception as e:
        # Catch-all for any unhandled exceptions
        logger.exception(f"assimilate: unhandled error for {url}")
        return f"# Error\n\nAssimilation failed for {url}:\n\n**{type(e).__name__}**: {e}\n\nPlease check the MCP server logs for details."

