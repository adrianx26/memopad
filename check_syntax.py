#!/usr/bin/env python3
"""Comprehensive syntax checker and fixer for Python files."""

import sys
from pathlib import Path

def check_and_fix_file(filepath):
    """Check a Python file for common syntax issues and fix them."""
    try:
        # Read with BOM handling
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        
        # Check if file compiles
        try:
            compile(content, filepath, 'exec')
            return True, "OK"
        except SyntaxError as e:
            # Record the error
            error_msg = f"Line {e.lineno}: {e.msg}"
            
            # Try to fix: remove BOM if present
            if content.startswith('\ufeff'):
                content = content[1:]
            
            # Re-save with proper encoding (no BOM)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            # Try compiling again
            try:
                compile(content, filepath, 'exec')
                return True, f"Fixed (was: {error_msg})"
            except SyntaxError as e2:
                return False, f"Line {e2.lineno}: {e2.msg}"
                
    except Exception as e:
        return False, str(e)

def main():
    """Scan and fix all Python files in src/memopad."""
    src_dir = Path('src/memopad')
    
    if not src_dir.exists():
        print(f"Directory {src_dir} not found!")
        return 1
    
    all_ok = True
    errors = []
    
    for py_file in src_dir.rglob('*.py'):
        rel_path = py_file.relative_to(Path.cwd())
        ok, msg = check_and_fix_file(py_file)
        
        if not ok:
            all_ok = False
            errors.append((str(rel_path), msg))
            print(f"❌ {rel_path}: {msg}")
        elif msg != "OK":
            print(f"✓ {rel_path}: {msg}")
    
    print(f"\n{'='*60}")
    if all_ok:
        print(" ✅ All Python files compile successfully!")
    else:
        print(f"❌ Found {len(errors)} files with syntax errors:")
        for filepath, error in errors:
            print(f"   - {filepath}: {error}")
    print(f"{'='*60}")
    
    return 0 if all_ok else 1

if __name__ == '__main__':
    sys.exit(main())
