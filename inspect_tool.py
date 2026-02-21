import sys
import os
sys.path.insert(0, os.path.abspath("src"))
from memopad.mcp.tools.assimilate import assimilate

print(f"Type: {type(assimilate)}")
print(f"Dir: {dir(assimilate)}")
if hasattr(assimilate, 'fn'):
    print(f"Found .fn: {assimilate.fn}")
if hasattr(assimilate, 'func'):
    print(f"Found .func: {assimilate.func}")
