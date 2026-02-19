import os
import shutil
from pathlib import Path

def merge_directories(source_root, target_root):
    """
    Merges content from source_root to target_root.
    Overwrites if source file is larger than target file.
    """
    source_path = Path(source_root)
    target_path = Path(target_root)

    if not source_path.exists():
        print(f"Source path does not exist: {source_path}")
        return

    print(f"Merging from {source_path} to {target_path}...")

    for root, dirs, files in os.walk(source_path):
        # Calculate relative path to maintain structure
        rel_path = Path(root).relative_to(source_path)
        current_target_dir = target_path / rel_path

        # Create target directory if it doesn't exist
        if not current_target_dir.exists():
            current_target_dir.mkdir(parents=True, exist_ok=True)
            # print(f"Created directory: {current_target_dir}")

        for file in files:
            source_file = Path(root) / file
            target_file = current_target_dir / file

            # Copy if target doesn't exist
            if not target_file.exists():
                shutil.copy2(source_file, target_file)
                print(f"Copied: {rel_path / file}")
            else:
                # Compare sizes if it does exist
                source_size = source_file.stat().st_size
                target_size = target_file.stat().st_size

                if source_size > target_size:
                    shutil.copy2(source_file, target_file)
                    print(f"Overwritten (larger): {rel_path / file} ({source_size} > {target_size})")
                else:
                    # print(f"Skipped (smaller/equal): {rel_path / file} ({source_size} <= {target_size})")
                    pass

def main():
    target_dir = r"C:\Users\shobymik\.memopad"
    sources = [
        r"C:\ANTI\p_temp\1_extracted",
        r"C:\ANTI\p_temp\2_extracted",
        r"C:\ANTI\p_temp\3_extracted"
    ]

    print(f"Target Directory: {target_dir}")
    
    for source in sources:
        merge_directories(source, target_dir)

    print("\nMerge completed.")

if __name__ == "__main__":
    main()
