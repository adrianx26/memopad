import os
import hashlib
import shutil
import argparse
import sys

# Hardcoded target directory as requested
ROOT_DIR = r"C:\Users\shobymik\.memopad"
# Directories to always exclude
EXCLUDE_DIRS = {'.git', '__pycache__', 'node_modules', '.vscode'}

def calculate_hash(filepath):
    """Calculates SHA256 hash of a file."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            while True:
                buf = f.read(65536)
                if not buf:
                    break
                hasher.update(buf)
        return hasher.hexdigest()
    except Exception as e:
        print(f"Error reading {filepath}: {e}")
        return None

def get_dir_content(root_dir):
    """
    Scans a directory recursively.
    Returns:
        set of (relative_path, file_hash) tuples
        dict mapping hash -> list of absolute paths
    """
    content_set = set()
    hash_map = {}
    
    # Normalize root_dir
    root_dir = os.path.abspath(root_dir)
    
    print(f"Scanning {root_dir}...")
    for dirpath, _, filenames in os.walk(root_dir):
        # Check if current directory should be skipped
        parts = dirpath.split(os.sep)
        if any(p in EXCLUDE_DIRS for p in parts):
            continue
            
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            file_hash = calculate_hash(filepath)
            
            if file_hash:
                # relative path from the root_dir
                rel_path = os.path.relpath(filepath, root_dir)
                content_set.add((rel_path, file_hash))
                
                if file_hash not in hash_map:
                    hash_map[file_hash] = []
                hash_map[file_hash].append(filepath)
                
    return content_set, hash_map

def find_superset(directories):
    """
    Identifies if one directory contains all files from the others.
    """
    dir_contents = {}
    
    # Scan all directories
    for d in directories:
        if not os.path.exists(d):
            print(f"Directory not found: {d}")
            continue
        
        content_set, _ = get_dir_content(d)
        if not content_set:
             print(f"  Empty directory: '{d}'")
             continue

        # Extract just hashes for comparison
        hashes = {h for _, h in content_set}
        dir_contents[d] = hashes
        print(f"  Found {len(hashes)} unique files in '{d}'")

    if not dir_contents:
        return None, []

    # Check for superset
    # Sort by number of files (descending)
    sorted_dirs = sorted(dir_contents.keys(), key=lambda k: len(dir_contents[k]), reverse=True)
    
    if not sorted_dirs:
         return None, []

    superset_candidate = sorted_dirs[0]
    superset_hashes = dir_contents[superset_candidate]
    
    safe_to_delete = []
    
    print("\n--- Analysis ---")
    print(f"Candidate for primary directory: '{superset_candidate}'")
    
    for other_dir in sorted_dirs[1:]:
        other_hashes = dir_contents[other_dir]
        missing = other_hashes - superset_hashes
        
        if not missing:
            print(f"  [SAFE] '{other_dir}' is fully contained in '{superset_candidate}'.")
            safe_to_delete.append(other_dir)
        else:
            print(f"  [UNSAFE] '{other_dir}' has {len(missing)} files NOT in '{superset_candidate}'.")

    return superset_candidate, safe_to_delete

def cleanup(dirs_to_clean, dry_run=True):
    print("\n--- Cleanup ---")
    if not dirs_to_clean:
        print("No directories identified for safe deletion.")
        return

    if dry_run:
        print("Dry Run: The following directories WOULD be deleted:")
        for d in dirs_to_clean:
            print(f"  - {d}")
        print("Run with --force to actually delete.")
    else:
        print("Deleting directories...")
        for d in dirs_to_clean:
            try:
                print(f"  Deleting {d}...")
                shutil.rmtree(d)
                print("  Deleted.")
            except Exception as e:
                print(f"  Failed to delete {d}: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=f"Safely identify and clean up duplicate directories in {ROOT_DIR}.")
    parser.add_argument("--force", action="store_true", help="Actually perform deletion (default is dry-run)")
    
    args = parser.parse_args()
    
    print(f"Configured Root Directory: {ROOT_DIR}")
    
    if not os.path.exists(ROOT_DIR):
        print(f"Error: configured root directory {ROOT_DIR} does not exist.")
        sys.exit(1)

    # Auto-discover subdirectories
    subdirs = []
    try:
        for d in os.listdir(ROOT_DIR):
            full_path = os.path.join(ROOT_DIR, d)
            if os.path.isdir(full_path) and d not in EXCLUDE_DIRS and not d.startswith('.'):
                subdirs.append(full_path)
    except Exception as e:
        print(f"Error accessing directories: {e}")
        sys.exit(1)
    
    if len(subdirs) < 2:
        print(f"Not enough subdirectories found in {ROOT_DIR} to compare (found {len(subdirs)}).")
        print("Directories found:", [os.path.basename(d) for d in subdirs])
        sys.exit(0)
        
    print(f"Scanning {len(subdirs)} subdirectories: {[os.path.basename(d) for d in subdirs]}\n")
        
    superset, safe_list = find_superset(subdirs)
    
    if superset:
        cleanup(safe_list, dry_run=not args.force)
    else:
        print("Could not perform analysis.")
