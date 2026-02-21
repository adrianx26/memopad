import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath("src"))

from memopad.config import ConfigManager

def main():
    config = ConfigManager().config
    print(f"kebab_filenames: {config.kebab_filenames}")
    print(f"database_path: {config.database_path}")
    print(f"config_dir: {ConfigManager().config_dir}")

if __name__ == "__main__":
    main()
