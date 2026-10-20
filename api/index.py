"""Vercel serverless entry point. Vercel looks for a WSGI callable named `app`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nursing_dss.web import create_app  # noqa: E402

app = create_app()
