"""Structured logging for assimilate operations.

Logs all operations, files saved, errors, and metadata to a JSON Lines file
for audit trails and debugging.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from loguru import logger as _logger


@dataclass
class AssimilateLogEntry:
    """A single log entry for an assimilate operation.
    
    This captures the full context of what was processed and what happened.
    """
    # Operation metadata
    timestamp: str
    url: str
    project: str
    project_path: str
    status: str  # "started", "completed", "failed", "partial"
    
    # Processing details
    strategy: str  # "github", "direct_download", "crawl"
    max_depth: int
    max_pages: int
    
    # Results
    items_processed: int = 0
    github_links_found: int = 0
    notes_created: int = 0
    notes_updated: int = 0
    notes_failed: int = 0
    
    # Detailed lists
    files_saved: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    
    # Timing
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    def to_json_line(self) -> str:
        """Convert to a JSON line for the log file."""
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)


class AssimilateLogger:
    """Logger for assimilate operations.
    
    Writes structured JSON Lines logs to a file for audit trails.
    Each line is a complete JSON object representing one assimilate operation.
    """
    
    def __init__(self, log_dir: Optional[Path] = None):
        """Initialize the logger.
        
        Args:
            log_dir: Directory to store log files. Defaults to ~/{data_dir_name}/logs/
        """
        if log_dir is None:
            # Default to ~/{data_dir_name}/logs/
            from memopad.config import DATA_DIR_NAME
            home = Path.home()
            log_dir = home / DATA_DIR_NAME / "logs"
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Log file with date rotation
        date_str = datetime.now().strftime('%Y-%m-%d')
        self.log_file = self.log_dir / f"assimilate_{date_str}.jsonl"
        
        # Current operation entry
        self._current_entry: Optional[AssimilateLogEntry] = None
        
        _logger.info(f"AssimilateLogger initialized: log_file={self.log_file}")
    
    def start_operation(
        self,
        url: str,
        project: str,
        project_path: str,
        strategy: str,
        max_depth: int,
        max_pages: int,
    ) -> AssimilateLogEntry:
        """Start logging a new assimilate operation.
        
        Args:
            url: The URL being assimilated
            project: Project name
            project_path: Full path to the project directory
            strategy: Detection strategy used (github, direct_download, crawl)
            max_depth: Max crawl depth
            max_pages: Max pages to fetch
            
        Returns:
            The log entry for this operation
        """
        now = datetime.now().isoformat()
        
        self._current_entry = AssimilateLogEntry(
            timestamp=now,
            url=url,
            project=project,
            project_path=str(project_path),
            status="started",
            strategy=strategy,
            max_depth=max_depth,
            max_pages=max_pages,
            started_at=now,
        )
        
        self._log_operation("start", f"Started assimilating {url}")
        _logger.info(f"Assimilate log started: url={url}, project={project}")
        
        return self._current_entry
    
    def log_file_saved(
        self,
        title: str,
        file_path: str,
        permalink: str,
        directory: str,
        operation: str = "created",  # "created" or "updated"
        content_length: int = 0,
    ) -> None:
        """Log a file that was successfully saved.

        Args:
            title: Note title
            file_path: Full path to the saved file
            permalink: Entity permalink
            directory: Directory where saved
            operation: "created" or "updated"
            content_length: Size of content in bytes
        """
        if self._current_entry is None:
            _logger.warning("log_file_saved: no operation in progress")
            return
        
        file_info = {
            "title": title,
            "file_path": file_path,
            "permalink": permalink,
            "directory": directory,
            "operation": operation,
            "content_length": content_length,
            "timestamp": datetime.now().isoformat(),
        }
        
        self._current_entry.files_saved.append(file_info)
        
        if operation == "created":
            self._current_entry.notes_created += 1
        else:
            self._current_entry.notes_updated += 1
        
        msg = f"{operation}: {title} at {file_path}"
        self._log_operation("file_saved", msg)
        _logger.debug(f"Logged file saved: {title} -> {file_path}")
    
    def log_error(
        self,
        error_type: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log an error during assimilation.
        
        Args:
            error_type: Type of error (e.g., "clone_failed", "write_failed")
            message: Error message
            details: Additional error details
        """
        if self._current_entry is None:
            _logger.warning("log_error called but no operation in progress")
            return
        
        error_info = {
            "type": error_type,
            "message": message,
            "timestamp": datetime.now().isoformat(),
            "details": details or {},
        }
        
        self._current_entry.errors.append(error_info)
        self._current_entry.notes_failed += 1
        
        self._log_operation("error", f"{error_type}: {message}")
        _logger.error(f"Logged assimilate error: {error_type} - {message}")
    
    def log_detection(
        self,
        url: str,
        detection_result: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log content detection results.
        
        Args:
            url: URL being processed
            detection_result: What was detected
            details: Detection details
        """
        if self._current_entry is None:
            return
        
        self._log_operation(
            "detection",
            f"Detected {detection_result} for {url}",
            details=details,
        )
    
    def log_processing(
        self,
        stage: str,
        message: str,
        count: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Log a processing stage.
        
        Args:
            stage: Processing stage (e.g., "crawl", "parse", "build_notes")
            message: Description
            count: Item count if applicable
            details: Additional details
        """
        if self._current_entry is None:
            return
        
        self._log_operation(
            f"processing_{stage}",
            message,
            count=count,
            details=details,
        )
    
    def _log_operation(
        self,
        operation_type: str,
        message: str,
        count: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Add an operation entry to the current log."""
        if self._current_entry is None:
            return

        op_info: dict[str, Any] = {
            "type": operation_type,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        }

        if count is not None:
            op_info["count"] = count
        if details is not None:
            op_info["details"] = details

        self._current_entry.operations.append(op_info)
    
    def complete_operation(
        self,
        status: str = "completed",
        items_processed: int = 0,
        github_links_found: int = 0,
    ) -> Optional[AssimilateLogEntry]:
        """Complete the current operation and write to log file.
        
        Args:
            status: Final status ("completed", "failed", "partial")
            items_processed: Number of pages/files processed
            github_links_found: Number of GitHub links found
            
        Returns:
            The completed log entry
        """
        if self._current_entry is None:
            _logger.warning("complete_operation: no operation in progress")
            return None
        
        # Update final stats
        self._current_entry.status = status
        self._current_entry.items_processed = items_processed
        self._current_entry.github_links_found = github_links_found
        self._current_entry.completed_at = datetime.now().isoformat()
        
        # Calculate duration
        if self._current_entry.started_at:
            start = datetime.fromisoformat(self._current_entry.started_at)
            end = datetime.now()
            duration = (end - start).total_seconds()
            self._current_entry.duration_seconds = duration
        
        # Write to log file
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(self._current_entry.to_json_line())
                f.write("\n")
            
            _logger.info(
                f"Assimilate log written: file={self.log_file}, "
                f"status={status}, notes={self._current_entry.notes_created}, "
                f"duration={self._current_entry.duration_seconds:.2f}s"
            )
        except Exception as e:
            _logger.error(f"Failed to write assimilate log: {e}")
        
        entry = self._current_entry
        self._current_entry = None
        return entry
    
    def get_log_summary(self, lines: int = 10) -> list[dict[str, Any]]:
        """Get recent log entries as a summary.
        
        Args:
            lines: Number of recent entries to return
            
        Returns:
            List of log entry dictionaries
        """
        if not self.log_file.exists():
            return []
        
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            
            # Get last N lines
            if len(all_lines) > lines:
                recent = all_lines[-lines:]
            else:
                recent = all_lines
            
            return [json.loads(line) for line in recent if line.strip()]
        except Exception as e:
            _logger.error(f"Failed to read log file: {e}")
            return []
    
    def get_log_file_path(self) -> Path:
        """Get the path to the current log file."""
        return self.log_file


# Global logger instance
_assimilate_logger: Optional[AssimilateLogger] = None


def get_logger(log_dir: Optional[Path] = None) -> AssimilateLogger:
    """Get or create the global assimilate logger instance.
    
    Args:
        log_dir: Optional custom log directory
        
    Returns:
        AssimilateLogger instance
    """
    global _assimilate_logger
    
    if _assimilate_logger is None:
        _assimilate_logger = AssimilateLogger(log_dir)
    
    return _assimilate_logger


def reset_logger() -> None:
    """Reset the global logger instance. Useful for testing."""
    global _assimilate_logger
    _assimilate_logger = None