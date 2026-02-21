with open("src/memopad/mcp/tools/assimilate.py", "r", encoding="utf-8") as f:
    content = f.read()

content = content.replace("<<<<<<< HEAD", "").replace(">>>>>>> 61574841bda51810032db92671842521082f00bd", "").replace("=======", "")

with open("src/memopad/mcp/tools/assimilate.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Conflicts fixed in src/memopad/mcp/tools/assimilate.py")