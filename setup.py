#!/usr/bin/env python3
"""Interactive setup script for OptionsTrader."""

import getpass
import os
import sys
from pathlib import Path
from shutil import copyfile

_PROJECT_ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(_PROJECT_ROOT))

BANNER = r"""
  ___        _   _                 _____              _
 / _ \ _ __ | |_(_) ___  _ __  __|_   _| __ __ _  __| | ___ _ __
| | | | '_ \| __| |/ _ \| '_ \/ __|| || '__/ _` |/ _` |/ _ \ '__|
| |_| | |_) | |_| | (_) | | | \__ \| || | | (_| | (_| |  __/ |
 \___/| .__/ \__|_|\___/|_| |_|___/|_||_|  \__,_|\__,_|\___|_|
      |_|
        Setup & Configuration
"""


def print_step(n: int, msg: str):
    print(f"\n[{n}] {msg}")
    print("-" * 50)


def main():
    print(BANNER)
    print("Setting up OptionsTrader...\n")

    # Step 1: Init database
    print_step(1, "Initializing database")
    try:
        from options_trader.db import init_db, DB_PATH
        init_db()
        print(f"  Database ready at: {DB_PATH}")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # Step 2: Check .env
    print_step(2, "Checking .env file")
    env_path = _PROJECT_ROOT / ".env"
    env_example = _PROJECT_ROOT / ".env.example"

    if not env_path.exists():
        if env_example.exists():
            copyfile(env_example, env_path)
            print(f"  Created .env from .env.example — please edit it with your API keys.")
        else:
            # Create minimal .env
            env_path.write_text(
                "POLYGON_API_KEY=your_polygon_api_key_here\n"
                "FLASK_SECRET_KEY=change_me_to_random_string\n"
                "DASHBOARD_PASSWORD_HASH=\n"
            )
            print(f"  Created minimal .env at {env_path}")
    else:
        print(f"  .env exists at {env_path}")

    # Step 3: Set up dashboard password
    print_step(3, "Dashboard password")
    env_content = env_path.read_text()

    hash_missing = (
        "DASHBOARD_PASSWORD_HASH=" not in env_content
        or "DASHBOARD_PASSWORD_HASH=\n" in env_content
        or env_content.split("DASHBOARD_PASSWORD_HASH=")[1].split("\n")[0].strip() == ""
    )

    if hash_missing:
        try:
            import bcrypt
        except ImportError:
            print("  bcrypt not installed. Run: pip install bcrypt")
            bcrypt = None

        if bcrypt:
            # Check if running non-interactively (stdin not a tty or SETUP_PASSWORD env var set)
            preset_password = os.getenv("SETUP_PASSWORD", "")
            if preset_password or not sys.stdin.isatty():
                password = preset_password or "test123"
                print(f"  Using preset password (non-interactive mode)")
            else:
                while True:
                    password = getpass.getpass("  Enter dashboard password: ")
                    confirm = getpass.getpass("  Confirm password: ")
                    if password == confirm:
                        break
                    print("  Passwords do not match. Try again.")

            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

            # Update .env
            if "DASHBOARD_PASSWORD_HASH=" in env_content:
                lines = env_content.splitlines(keepends=True)
                new_lines = []
                for line in lines:
                    if line.startswith("DASHBOARD_PASSWORD_HASH="):
                        new_lines.append(f"DASHBOARD_PASSWORD_HASH={hashed}\n")
                    else:
                        new_lines.append(line)
                env_path.write_text("".join(new_lines))
            else:
                with open(env_path, "a") as f:
                    f.write(f"\nDASHBOARD_PASSWORD_HASH={hashed}\n")

            print("  Password hash written to .env")
        else:
            print("  Skipping password setup (bcrypt unavailable)")
    else:
        print("  DASHBOARD_PASSWORD_HASH already set")

    # Step 4: Create reports directory
    print_step(4, "Creating reports/daily/ directory")
    reports_dir = _PROJECT_ROOT / "reports" / "daily"
    reports_dir.mkdir(parents=True, exist_ok=True)
    print(f"  {reports_dir}")

    # Step 5: Create launchd plist for macOS scheduling
    print_step(5, "Creating launchd plist (macOS scheduler)")
    venv_python = _PROJECT_ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        # Try system python
        venv_python = Path(sys.executable)

    scheduler_path = _PROJECT_ROOT / "scheduler.py"
    log_dir = _PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    plist_label = "com.optionstrader.scheduler"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{plist_label}.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
    "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{plist_label}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{venv_python}</string>
        <string>{scheduler_path}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{_PROJECT_ROOT}</string>

    <key>CalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>16</integer><key>Minute</key><integer>15</integer></dict>
    </array>

    <key>StandardOutPath</key>
    <string>{log_dir}/scheduler_launchd.log</string>

    <key>StandardErrorPath</key>
    <string>{log_dir}/scheduler_launchd_err.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:{venv_python.parent}</string>
    </dict>
</dict>
</plist>
"""
    plist_path.write_text(plist_content)
    print(f"  Plist written to: {plist_path}")
    print(f"\n  NOTE: The scheduler is NOT loaded automatically.")
    print(f"  To enable it, run:")
    print(f"    launchctl load {plist_path}")
    print(f"  To disable:")
    print(f"    launchctl unload {plist_path}")

    # Summary
    print("\n" + "=" * 60)
    print("Setup complete!")
    print("=" * 60)
    print(f"  Database:    {_PROJECT_ROOT / 'data' / 'options_trader.db'}")
    print(f"  Reports:     {reports_dir}")
    print(f"  Logs:        {log_dir}")
    print(f"  Plist:       {plist_path}")
    print()
    print("Next steps:")
    print("  1. Edit .env with your POLYGON_API_KEY and SMTP settings")
    print("  2. Start the web dashboard: python web/app.py")
    print("  3. Run the scheduler manually: python scheduler.py --dry-run")
    print("  4. Screen candidates: python main.py candidates fill")
    print()


if __name__ == "__main__":
    main()
