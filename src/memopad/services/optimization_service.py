"""Storage optimization service for Memopad.

Provides storage optimization operations like duplicate detection, file compression,
and size optimization for Memopad knowledge bases.
"""

from dataclasses import dataclass
from pathlib import Path
import os
import hashlib
from loguru import logger


@dataclass
class StorageUsage:
    """Represents storage usage statistics."""
    
    total_files: int
    total_size: int
    avg_file_size: float
    largest_file_size: int
    largest_filename: str


@dataclass
class OptimizationResult:
    """Represents the results of a storage optimization operation."""
    
    processed_count: int
    optimized_count: int
    storage_saved: int
    reduction_percentage: float
    optimized_files: list
    skipped_files: list
    errors: list


class StorageOptimizer:
    """Service for optimizing Memopad storage."""
    
    def __init__(self, project_config):
        """Initialize storage optimizer with project configuration."""
        self.project_config = project_config
        # Handle both Project model (path) and ProjectConfig (home)
        project_path = getattr(project_config, 'path', None) or getattr(project_config, 'home', None)
        if not project_path:
            raise ValueError("Project configuration must have 'path' or 'home' attribute")
        self.project_path = Path(project_path)
        
    async def get_storage_usage(self) -> StorageUsage:
        """Get storage usage statistics for the project."""
        logger.debug(f"Calculating storage usage for {self.project_config.name}")
        
        total_files = 0
        total_size = 0
        largest_file = 0
        largest_filename = ""
        file_sizes = []
        
        for dirpath, _, filenames in os.walk(self.project_path):
            for filename in filenames:
                file_path = Path(dirpath) / filename
                try:
                    file_size = file_path.stat().st_size
                    total_files += 1
                    total_size += file_size
                    file_sizes.append(file_size)
                    
                    if file_size > largest_file:
                        largest_file = file_size
                        largest_filename = file_path.relative_to(self.project_path)
                        
                except Exception as e:
                    logger.warning(f"Error accessing {file_path}: {e}")
        
        avg_size = 0
        if total_files > 0:
            avg_size = total_size / total_files
        
        return StorageUsage(
            total_files=total_files,
            total_size=total_size,
            avg_file_size=avg_size,
            largest_file_size=largest_file,
            largest_filename=str(largest_filename)
        )
    
    async def optimize(self) -> OptimizationResult:
        """Run storage optimization."""
        logger.debug(f"Starting storage optimization for {self.project_config.name}")
        
        processed_count = 0
        optimized_count = 0
        storage_saved = 0
        optimized_files = []
        skipped_files = []
        errors = []
        
        # Check for duplicate files
        file_hashes = {}
        for dirpath, _, filenames in os.walk(self.project_path):
            for filename in filenames:
                file_path = Path(dirpath) / filename
                relative_path = file_path.relative_to(self.project_path)
                
                try:
                    processed_count += 1
                    
                    # Skip temporary and hidden files
                    if filename.startswith('.') or filename.endswith('~'):
                        skipped_files.append(str(relative_path))
                        continue
                        
                    # Compute hash for duplicate detection
                    with open(file_path, 'rb') as f:
                        file_hash = hashlib.md5(f.read()).hexdigest()
                        
                    if file_hash in file_hashes:
                        duplicate_path = file_hashes[file_hash]
                        logger.info(f"Duplicate file found: {relative_path} duplicates {duplicate_path}")
                        
                        # For now, just log duplicates, don't delete automatically
                        skipped_files.append(str(relative_path))
                    else:
                        file_hashes[file_hash] = str(relative_path)
                        
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {e}")
                    errors.append(str(relative_path) + f": {e}")
        
        # Calculate optimization statistics
        reduction_percentage = 0
        if processed_count > 0:
            # For now, we're just detecting duplicates, not optimizing
            # So reduction percentage is 0
            reduction_percentage = 0
        
        return OptimizationResult(
            processed_count=processed_count,
            optimized_count=optimized_count,
            storage_saved=storage_saved,
            reduction_percentage=reduction_percentage,
            optimized_files=optimized_files,
            skipped_files=skipped_files,
            errors=errors
        )
