#!/usr/bin/env python3
"""Comprehensive fix for all syntax errors from quote replacement."""

from pathlib import Path

def fix_file(filepath):
    """Fix escape sequence issues in a Python file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        
        # Check if there are any suspicious patterns
        if '\\\\\\\\"' in content or '\\"' in content:
            # Fix multiple backslash escalations
            # This pattern matches the broken \\\\" and restores it
            content = content.replace('\\\\\\\\"', '\\\\"')
            
        #  Check if file compiles
        try:
            compile(content, filepath, 'exec')
            if content != original_content:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                return True, "Fixed"
            return True, "OK"
        except SyntaxError:
            # File still has issues, skip
            return False, "Syntax error"
            
    except Exception as e:
        return False, str(e)

def main():
    """Fix all Python files in src/memopad."""
    src_dir = Path('src/memopad')
    
    fixed = []
    errors = []
    
    for py_file in src_dir.rglob('*.py'):
        ok, msg = fix_file(py_file)
        if not ok:
            errors.append((str(py_file), msg))
        elif msg == "Fixed":
            fixed.append(str(py_file))
    
    if fixed:
        print(f"✓ Fixed {len(fixed)} files:")
        for f in fixed:
            print(f"  - {f}")
    
    if errors:
        print(f"\n✗ {len(errors)} files with errors:")
        for f, err in errors:
            print(f"  - {f}: {err}")
        return 1
    
    print("\n✅ All files compile successfully!")
    return 0

if __name__ == '__main__':
    exit(main())
