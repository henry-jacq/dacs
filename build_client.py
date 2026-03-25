#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil

def main():
    print("========================================")
    print("DACS Native Client Builder")
    print("========================================")
    
    server_url = input("Enter Server URL [ws://127.0.0.1:8080/ws]: ").strip() or "ws://127.0.0.1:8080/ws"
    agent_token = input("Enter Auth Token [change-me-agent-token]: ").strip() or "change-me-agent-token"
    client_id = input("Enter Target Client ID [auto-generate]: ").strip()
    
    print("\nSelect target platform environment:")
    print("  1) Linux (Native)")
    print("  2) Windows (Native/Wine)")
    plat_choice = input("Choice [1]: ").strip() or "1"
    
    print("\n[*] Generating embedded payload configuration...")
    
    os.makedirs("dist", exist_ok=True)
    payload_path = "dist/dacs_payload.py"
    
    with open(payload_path, "w") as f:
        f.write(f"""import os
import sys
import platform
import random
import string

# Hardcoded environment configurations
os.environ["DACS_SERVER_URL"] = "{server_url}"
os.environ["DACS_AGENT_TOKEN"] = "{agent_token}"
""")
        if client_id:
            f.write(f'os.environ["DACS_CLIENT_ID"] = "{client_id}"\n')
        else:
            f.write('os.environ["DACS_CLIENT_ID"] = f"node-{platform.node()}-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=4))\n')

        f.write("""
import logging
from client.app.agent import Agent
from client.app.config import load_settings
from client.app.logger import configure_logging

def start():
    configure_logging()
    logging.getLogger("websockets").setLevel(logging.ERROR)
    
    settings = load_settings()
    Agent(settings).run_forever()

if __name__ == "__main__":
    start()
""")

    print("[*] Checking native compiler dependencies...")
    try:
        import nuitka
    except ImportError:
        print("[!] Nuitka native compiler not found. Installing via pip...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "nuitka"])
        
    os_name = "linux" if plat_choice == "1" else "windows.exe"
    print(f"[*] Compiling natively via Nuitka for {os_name}...")
    
    binary_name = f"dacs_agent_{os_name}"
    target_path = os.path.join("dist", binary_name)
    
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--onefile",
        "--remove-output",
        "-o", target_path,
        "--disable-console",
        payload_path
    ]
    
    if plat_choice == "2" and sys.platform != "win32":
        print("\n[!] WARNING: You selected Windows but are running Linux.")
        print("    Nuitka translates Python to C and strictly requires a Windows C-Compiler natively (or MinGW/Wine).")
        print("    Attempting completion. Execution may yield a Linux ELF binary targeting the fallback GCC.")
        
    try:
        print(f"\n[*] Executing: {' '.join(cmd)}")
        subprocess.check_call(cmd)
        print(f"\n[+] SUCCESS! Standalone native binary compiled to C and packed: {target_path}")
    except subprocess.CalledProcessError as e:
        print(f"\n[-] Build failed with exit code {e.returncode}")
        print("[-] Ensure you have a valid C compiler installed (GCC on Linux, MSVC/MinGW on Windows).")
    finally:
        try:
            os.remove(payload_path)
            shutil.rmtree("dist/dacs_payload.build", ignore_errors=True)
            shutil.rmtree("dist/dacs_payload.dist", ignore_errors=True)
            shutil.rmtree("dist/dacs_payload.onefile-build", ignore_errors=True)
        except Exception:
            pass

if __name__ == "__main__":
    main()
