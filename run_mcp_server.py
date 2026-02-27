from memopad.cli.main import app
import sys

if __name__ == "__main__":
    sys.argv = ['memopad', 'mcp', '--transport', 'streamable-http', '--host', '0.0.0.0', '--port', '8000']
    app()
