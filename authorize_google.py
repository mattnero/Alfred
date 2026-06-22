"""One-time Google OAuth consent for Alfred's brain (run on the PC).

This creates the stored token Alfred's GoogleAPIClient uses to read and write the
user's *personal* Google Calendar and Tasks. Run it once, in a browser, on the
machine that will be the brain. It needs an OAuth *client secrets* file from a
Google Cloud project (Desktop-app credentials) — see the README runbook for how
to obtain one.

    ~/assistant-env/bin/python authorize_google.py
    python authorize_google.py --credentials path\\to\\credentials.json --token path\\to\\token.json

The browser opens, you grant access, and the resulting token is written to
~/.alfred/google_token.json (override with --token). brain_server picks it up
automatically on its next start.
"""
from __future__ import annotations

import argparse
import os

from google_tools import DEFAULT_CREDENTIALS_PATH, DEFAULT_TOKEN_PATH, SCOPES


def authorize(credentials_path: str, token_path: str) -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not os.path.exists(credentials_path):
        raise SystemExit(
            f"OAuth client secrets not found at {credentials_path}.\n"
            "Create a Desktop-app OAuth client in Google Cloud Console, download "
            "the JSON, and save it there (see the README runbook)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)
    os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
    with open(token_path, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    print(f"Authorized, sir. Token saved to {token_path}.")


def main() -> None:
    ap = argparse.ArgumentParser(description="One-time Google OAuth consent for Alfred")
    ap.add_argument("--credentials", default=DEFAULT_CREDENTIALS_PATH,
                    help="OAuth client secrets JSON (Desktop app)")
    ap.add_argument("--token", default=DEFAULT_TOKEN_PATH,
                    help="where to write the authorized token")
    args = ap.parse_args()
    authorize(args.credentials, args.token)


if __name__ == "__main__":
    main()
