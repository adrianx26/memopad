#!/usr/bin/env python3
"""Fix the specific syntax error on line 70 of test_upload.py."""

# Read the file
with open('tests/cli/test_upload.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 70 is index 69 (0-indexed)
# Current broken: assert "\\" not in remote_path  # No backslashes
# Should be:      assert "\\\\" not in remote_path  # No backslashes
original_line = lines[69]
print(f"Original line 70: {original_line!r}")

# Fix the line by replacing the broken pattern
# The '\\"' pattern (escaped backslash followed by quote) needs to be '\\\\' (double backslash)
fixed_line = '        assert "\\\\\\\\" not in remote_path  # No backslashes\n'
lines[69] = fixed_line

print(f"Fixed line 70: {fixed_line!r}")

# Write back
with open('tests/cli/test_upload.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Fix applied!")

# Verify
import py_compile
try:
    py_compile.compile('tests/cli/test_upload.py', doraise=True)
    print("File compiles successfully!")
except SyntaxError as e:
    print(f"Still has syntax error: {e}")
