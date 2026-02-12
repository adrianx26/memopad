#!/usr/bin/env python3
"""Comprehensive syntax checker and fixer for all Python files."""

import os
import sys
from pathlib import Path

def check_syntax(filepath):
    """Check if a Python file has syntax errors."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        compile(content, filepath, 'exec')
        return True, None, None
    except SyntaxError as e:
        return False, e.lineno, e.msg
    except Exception as e:
        return False, None, str(e)

def get_line(filepath, lineno):
    """Get a specific line from a file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if lineno and 0 < lineno <= len(lines):
            return lines[lineno - 1].rstrip()
        return None
    except Exception:
        return None

def main():
    """Scan all Python files and report syntax errors."""
    src_dirs = ['src/memopad', 'tests', 'test-int']
    
    all_files = []
    for src_dir in src_dirs:
        if os.path.exists(src_dir):
            for py_file in Path(src_dir).rglob('*.py'):
                all_files.append(py_file)
    
    print(f"Scanning {len(all_files)} Python files...")
    print("=" * 70)
    
    errors = []
    ok_count = 0
    
    for py_file in all_files:
        ok, lineno, msg = check_syntax(py_file)
        if not ok:
            line_content = get_line(py_file, lineno)
            errors.append({
                'file': str(py_file),
                'line': lineno,
                'msg': msg,
                'content': line_content
            })
            print(f"ERROR: {py_file}")
            print(f"  Line {lineno}: {msg}")
            if line_content:
                print(f"  Content: {line_content[:80]}...")
            print()
        else:
            ok_count += 1
    
    print("=" * 70)
    print(f"Results: {ok_count} OK, {len(errors)} with errors")
    
    if errors:
        print("\nFiles with syntax errors:")
        for err in errors:
            print(f"  {err['file']}:{err['line']}")
        return 1
    else:
        print("\nAll files compile successfully!")
        return 0

if __name__ == '__main__':
    sys.exit(main())
