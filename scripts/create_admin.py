#!/usr/bin/env python3
"""Create or promote a user to admin.

Usage:
    python scripts/create_admin.py <username> [password]

If password is omitted, a random one is generated and printed.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import metadb as meta
import auth as auth_mod


async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/create_admin.py <username> [password]", file=sys.stderr)
        sys.exit(1)

    username = sys.argv[1].strip()
    password = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    if not password:
        import secrets
        import string
        password = secrets.token_urlsafe(16)
        print(f"Generated password: {password}", file=sys.stderr)

    await meta.init_db()
    hashed = auth_mod.hash_password(password)
    user_id = await meta.create_user(username, hashed)

    if user_id is None:
        existing = await meta.get_user_by_username(username)
        if not existing:
            print(f"Error: could not find or create user '{username}'", file=sys.stderr)
            sys.exit(1)
        user_id = existing["id"]
        print(f"User '{username}' already exists (id={user_id}). Promoting to admin.")
    else:
        print(f"Created user '{username}' (id={user_id}).")

    await meta.set_user_role(user_id, "admin")
    print(f"User '{username}' (id={user_id}) is now an admin.")
    print(f"Password: {password}")


if __name__ == "__main__":
    asyncio.run(main())
