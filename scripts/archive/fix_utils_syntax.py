#!/usr/bin/env python3
"""Fix syntax error in utils.py line 408."""


# Read the file
with open('src/memopad/utils.py', 'r', encoding='utf-8') as f:
    content = f.read()

# The broken line pattern - need to escape backslashes properly
# Current broken state might be: if "\\..\" in path or path.startswith("\") or path.startswith("\"):
# Should be: if "\\.." in path or path.startswith("\\\\") or path.startswith("\\"):

# Find and fix the specific line
lines = content.split('\n')
for i, line in enumerate(lines):
    # Look for the problematic line around line 407-408
    if 'Check for Windows-style path traversal' in line:
        # Fix the next line
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            # Replace the broken line with the correct version
            if 'if' in next_line and 'path' in next_line:
                lines[i + 1] = '    if "\\\\\\".." in path or path.startswith("\\\\\\\\") or path.startswith("\\\\"):'
                print(f"Fixed line {i + 2}: {lines[i + 1]}")
                break

# Write back
with open('src/memopad/utils.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(lines))

print("Fix applied successfully!")
