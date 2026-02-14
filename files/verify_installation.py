#!/usr/bin/env python3
"""
Memopad MCP Server Installation Verification Script
Run this to verify your installation is working correctly
"""

import json
import sys
from pathlib import Path
import subprocess
import os

class Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    """Print formatted header"""
    print(f"\n{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{text}{Colors.RESET}")
    print(f"{Colors.CYAN}{Colors.BOLD}{'='*60}{Colors.RESET}\n")

def print_success(text):
    """Print success message"""
    print(f"{Colors.GREEN}✓ {text}{Colors.RESET}")

def print_error(text):
    """Print error message"""
    print(f"{Colors.RED}✗ {text}{Colors.RESET}")

def print_warning(text):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.RESET}")

def print_info(text):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ {text}{Colors.RESET}")

def check_python_version():
    """Check if Python version is adequate"""
    print_header("Checking Python Version")
    
    version = sys.version_info
    version_string = f"{version.major}.{version.minor}.{version.micro}"
    
    print(f"Python version: {version_string}")
    
    if version.major >= 3 and version.minor >= 10:
        print_success("Python version is adequate (3.10+)")
        return True
    else:
        print_error(f"Python version too old. Need 3.10+, found {version_string}")
        return False

def check_installation_directory():
    """Check if installation directory exists"""
    print_header("Checking Installation Directory")
    
    # Common installation paths
    if sys.platform == "win32":
        install_paths = [
            Path("F:/ANTI/memopad"),
            Path.home() / ".mcp-servers" / "memopad",
        ]
    else:
        install_paths = [
            Path.home() / "mcp-servers" / "memopad",
            Path.home() / ".mcp-servers" / "memopad",
        ]
    
    found = False
    install_dir = None
    
    for path in install_paths:
        if path.exists():
            print_success(f"Found installation: {path}")
            install_dir = path
            found = True
            break
    
    if not found:
        print_error("Installation directory not found")
        print_info("Expected locations:")
        for path in install_paths:
            print(f"  - {path}")
        return None
    
    return install_dir

def check_server_file(install_dir):
    """Check if server file exists and is valid"""
    print_header("Checking Server File")
    
    if install_dir is None:
        print_error("No installation directory provided")
        return False
    
    server_files = ["server.py", "memopad_server_fixed.py"]
    server_path = None
    
    for filename in server_files:
        path = install_dir / filename
        if path.exists():
            print_success(f"Found server file: {path}")
            server_path = path
            break
    
    if server_path is None:
        print_error("Server file not found")
        return False
    
    # Check if file is valid Python
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(server_path)],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print_success("Server file syntax is valid")
            return True
        else:
            print_error(f"Server file has syntax errors: {result.stderr}")
            return False
    except Exception as e:
        print_warning(f"Could not validate server file: {e}")
        return True  # Continue anyway

def check_storage_directory():
    """Check if storage directory exists"""
    print_header("Checking Storage Directory")
    
    storage_dir = Path.home() / ".memopad"
    
    if storage_dir.exists():
        print_success(f"Storage directory exists: {storage_dir}")
        
        notes_file = storage_dir / "notes.json"
        if notes_file.exists():
            print_success(f"Notes file exists: {notes_file}")
            
            # Check if it's valid JSON
            try:
                with open(notes_file, 'r', encoding='utf-8') as f:
                    notes = json.load(f)
                print_success(f"Notes file is valid JSON ({len(notes)} notes)")
            except json.JSONDecodeError:
                print_warning("Notes file exists but contains invalid JSON")
            except Exception as e:
                print_warning(f"Could not read notes file: {e}")
        else:
            print_info("Notes file doesn't exist yet (will be created on first use)")
        
        return True
    else:
        print_warning(f"Storage directory doesn't exist: {storage_dir}")
        print_info("It will be created automatically on first use")
        return False

def check_claude_config():
    """Check Claude Desktop configuration"""
    print_header("Checking Claude Desktop Configuration")
    
    if sys.platform == "win32":
        config_path = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    elif sys.platform == "darwin":
        config_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        config_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"
    
    print(f"Config location: {config_path}")
    
    if not config_path.exists():
        print_warning("Claude Desktop config file not found")
        print_info("You need to create it manually or restart Claude Desktop")
        return False
    
    print_success("Config file exists")
    
    # Check if memopad is configured
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if "mcpServers" in config and "memopad" in config["mcpServers"]:
            print_success("Memopad is configured in Claude Desktop")
            
            memopad_config = config["mcpServers"]["memopad"]
            print(f"\n  Command: {memopad_config.get('command', 'N/A')}")
            print(f"  Args: {memopad_config.get('args', 'N/A')}")
            
            return True
        else:
            print_warning("Memopad not found in Claude Desktop config")
            print_info("You need to add the memopad server to your config")
            return False
    
    except json.JSONDecodeError:
        print_error("Config file contains invalid JSON")
        return False
    except Exception as e:
        print_error(f"Could not read config file: {e}")
        return False

def run_basic_test(install_dir):
    """Run a basic server test"""
    print_header("Running Basic Server Test")
    
    if install_dir is None:
        print_error("No installation directory provided")
        return False
    
    server_path = install_dir / "server.py"
    if not server_path.exists():
        server_path = install_dir / "memopad_server_fixed.py"
    
    if not server_path.exists():
        print_error("Server file not found")
        return False
    
    print("Testing JSON-RPC initialize method...")
    
    try:
        # Test initialize method
        test_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {}
        }
        
        process = subprocess.Popen(
            [sys.executable, str(server_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Send test request
        stdout, stderr = process.communicate(
            input=json.dumps(test_request) + "\n",
            timeout=5
        )
        
        # Parse response
        try:
            response = json.loads(stdout.strip())
            if "result" in response and "protocolVersion" in response["result"]:
                print_success("Server responded correctly to initialize")
                return True
            else:
                print_warning("Server response missing expected fields")
                return False
        except json.JSONDecodeError:
            print_error("Server response is not valid JSON")
            print(f"Response: {stdout}")
            return False
    
    except subprocess.TimeoutExpired:
        print_error("Server test timed out")
        process.kill()
        return False
    except Exception as e:
        print_error(f"Server test failed: {e}")
        return False

def run_full_tests(install_dir):
    """Run full test suite if available"""
    print_header("Running Full Test Suite")
    
    if install_dir is None:
        print_error("No installation directory provided")
        return False
    
    test_path = install_dir / "test_memopad.py"
    
    if not test_path.exists():
        print_warning("Test suite not found")
        print_info(f"Expected location: {test_path}")
        return False
    
    print(f"Running tests from: {test_path}\n")
    
    try:
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        print(result.stdout)
        
        if result.returncode == 0:
            print_success("All tests passed!")
            return True
        else:
            print_error("Some tests failed")
            if result.stderr:
                print(result.stderr)
            return False
    
    except subprocess.TimeoutExpired:
        print_error("Test suite timed out")
        return False
    except Exception as e:
        print_error(f"Could not run tests: {e}")
        return False

def print_summary(results):
    """Print verification summary"""
    print_header("Verification Summary")
    
    passed = sum(results.values())
    total = len(results)
    
    for check, result in results.items():
        if result:
            print_success(check)
        else:
            print_error(check)
    
    print(f"\n{Colors.BOLD}Overall: {passed}/{total} checks passed{Colors.RESET}")
    
    if passed == total:
        print(f"\n{Colors.GREEN}{Colors.BOLD}✓ Installation is complete and working!{Colors.RESET}")
        print(f"\n{Colors.CYAN}Next steps:{Colors.RESET}")
        print("1. Restart Claude Desktop")
        print("2. Test by asking: 'Create a note titled Test with content Hello World'")
    else:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠ Some issues were found{Colors.RESET}")
        print(f"\n{Colors.CYAN}Please review the errors above and fix them{Colors.RESET}")

def main():
    """Main verification function"""
    print(f"{Colors.BOLD}{Colors.CYAN}")
    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Memopad MCP Server - Installation Verification         ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(Colors.RESET)
    
    results = {}
    
    # Run checks
    results["Python version"] = check_python_version()
    
    install_dir = check_installation_directory()
    results["Installation directory"] = install_dir is not None
    
    results["Server file"] = check_server_file(install_dir)
    results["Storage directory"] = check_storage_directory()
    results["Claude config"] = check_claude_config()
    
    # Optional tests
    print("\n")
    run_tests = input(f"{Colors.YELLOW}Run basic server test? (y/n): {Colors.RESET}").lower().strip()
    if run_tests == 'y':
        results["Basic server test"] = run_basic_test(install_dir)
    
    print("\n")
    run_full = input(f"{Colors.YELLOW}Run full test suite? (y/n): {Colors.RESET}").lower().strip()
    if run_full == 'y':
        results["Full test suite"] = run_full_tests(install_dir)
    
    # Print summary
    print_summary(results)
    
    return all(results.values())

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Verification cancelled by user{Colors.RESET}")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n{Colors.RED}Unexpected error: {e}{Colors.RESET}")
        sys.exit(1)
