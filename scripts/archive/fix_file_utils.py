#!/usr/bin/env python3
"""Fix the specific syntax error on line 469 of file_utils.py."""

# Read the file
with open('src/memopad/file_utils.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 469 is index 468 (0-indexed)
original_line = lines[468]
print(f"Original line 469: {original_line!r}")

# The line should be:
#         c for c in sanitized if c.isalnum() or c in (".", " ", "-", "_", "\\", "/")
# But it currently has escaped quotes like:  c in (\".\", \" \", \"-\", \"_\", \"\\\", \"/\")

# Replace the broken line
fixed_line = '        c for c in sanitized if c.isalnum() or c in (".", " ", "-", "_", "\\\\", "/")\n'
lines[468] = fixed_line

print(f"Fixed line 469: {fixed_line!r}")

# Write back
with open('src/memopad/file_utils.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("✓ Fixed file_utils.py line 469")

# Verify
import py_compile
try:
    py_compile.compile('src/memopad/file_utils.py', doraise=True)
    print("✓ File compiles successfully!")
except SyntaxError as e:
    print(f"✗ Still has syntax error: {e}")
