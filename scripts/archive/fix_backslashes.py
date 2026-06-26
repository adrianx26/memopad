#!/usr/bin/env python3
"""
Fix all broken backslash escapes in Python files.

The rename operation broke lines like:
    "\\",  ->  "\",   (missing backslash)
    
This script finds and fixes these patterns.
"""

import os
import re
from pathlib import Path

def fix_broken_backslash_strings(content):
    """Fix broken backslash escape sequences."""
    lines = content.split('\n')
    fixed_lines = []
    changed = False
    
    for i, line in enumerate(lines):
        original_line = line
        
        # Pattern 1: Fix standalone backslash in list/tuple that's broken
        # e.g., '            "\",' should become '            "\\",'
        if re.search(r'"\\",$', line.strip()):
            # This is the broken pattern - a single backslash followed by double quote and comma
            line = re.sub(r'"\\",$', r'"\\\\",', line)
        
        # Pattern 2: Fix backslash in character sets like c in (".", " ", "-", "_", "\", "/")  
        # Should be: c in (".", " ", "-", "_", "\\", "/")
        if re.search(r'"\\",\s*"/"', line):
            line = line.replace('"\\", "/"', '"\\\\", "/"')
        
        # Pattern 3: Fix path checks like path.startswith("\")
        # Should be: path.startswith("\\")
        if re.search(r'\.startswith\("\\"\)', line):
            line = line.replace('.startswith("\\")', '.startswith("\\\\")')
        
        # Pattern 4: Fix "\\..\" patterns - should be "\\.." 
        if '"\\\\..\\"' in line:
            line = line.replace('"\\\\..\\"', '"\\\\.."')
        
        # Pattern 5: Fix path.startswith("\\\\") which became path.startswith("")
        if 'path.startswith("")' in line and 'startswith' in line:
            # This needs context - skip for now
            pass
            
        if line != original_line:
            changed = True
            print(f"  Fixed line {i+1}")
            
        fixed_lines.append(line)
    
    return '\n'.join(fixed_lines), changed


def fix_file(filepath):
    """Fix a single Python file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # First, try to compile to see if it has errors
        try:
            compile(content, str(filepath), 'exec')
            return True, "OK"
        except SyntaxError:
            pass  # Continue to fix
        
        # Apply fixes
        fixed_content, changed = fix_broken_backslash_strings(content)
        
        if changed:
            # Write back and verify
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(fixed_content)
            
            # Verify it compiles now
            try:
                compile(fixed_content, str(filepath), 'exec')
                return True, "Fixed"
            except SyntaxError as e:
                return False, f"Still broken at line {e.lineno}: {e.msg}"
        else:
            return False, "Could not auto-fix"
            
    except Exception as e:
        return False, str(e)


def main():
    """Fix all Python files."""
    src_dirs = ['src/memopad', 'tests', 'test-int']
    
    all_files = []
    for src_dir in src_dirs:
        if os.path.exists(src_dir):
            for py_file in Path(src_dir).rglob('*.py'):
                all_files.append(py_file)
    
    print(f"Scanning {len(all_files)} Python files for syntax errors...")
    print("=" * 70)
    
    fixed_count = 0
    error_files = []
    
    for py_file in all_files:
        # First check if file has errors
        try:
            with open(py_file, 'r', encoding='utf-8') as f:
                content = f.read()
            compile(content, str(py_file), 'exec')
            continue  # No errors, skip
        except SyntaxError:
            pass
        
        print(f"Fixing: {py_file}")
        ok, msg = fix_file(py_file)
        
        if ok and msg == "Fixed":
            fixed_count += 1
            print("  -> Fixed!")
        elif not ok:
            error_files.append((str(py_file), msg))
            print(f"  -> {msg}")
    
    print("=" * 70)
    print(f"Fixed {fixed_count} files")
    
    if error_files:
        print(f"\n{len(error_files)} files still have errors:")
        for f, msg in error_files:
            print(f"  {f}: {msg}")
        return 1
    else:
        print("\nAll files now compile successfully!")
        return 0

if __name__ == '__main__':
    exit(main())
