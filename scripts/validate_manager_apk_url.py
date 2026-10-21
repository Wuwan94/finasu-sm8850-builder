#!/usr/bin/env python3
"""Validate the optional workflow URL before passing it to curl."""

import sys
from urllib.parse import urlsplit


def valid_https_url(value):
    if not value or any(char.isspace() or ord(char) < 0x20 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        # Accessing .port also rejects malformed numeric ports.
        _ = parsed.port
    except ValueError:
        return False
    return (parsed.scheme.lower() == "https" and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not valid_https_url(args[0]):
        print("manager_apk_url must be an absolute HTTPS URL without credentials or whitespace",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
