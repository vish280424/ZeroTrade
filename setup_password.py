"""
Run once to generate a bcrypt hash for your app password.
Paste the output into .env as APP_PASSWORD_HASH=...

Usage:
  python setup_password.py
"""
import getpass
import bcrypt

password = getpass.getpass("Set app password: ")
confirm  = getpass.getpass("Confirm password: ")

if password != confirm:
    print("Passwords do not match.")
    raise SystemExit(1)

hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
print(f"\nAdd this to your .env:\n\nAPP_PASSWORD_HASH={hashed}\n")
