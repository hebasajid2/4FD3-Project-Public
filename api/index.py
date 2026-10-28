"""Vercel entry point for the nursing application's API."""

import os
import sys
from urllib.parse import parse_qsl, urlencode

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
)

from nursing_dss.web import create_app

application = create_app()


def app(environ, start_response):
    environ = environ.copy()

    query = parse_qsl(
        environ.get("QUERY_STRING", ""),
        keep_blank_values=True,
    )

    route = None
    remaining_query = []

    for key, value in query:
        if key == "__route":
            route = value
        else:
            remaining_query.append((key, value))

    if route is not None:
        environ["PATH_INFO"] = "/api/" + route.lstrip("/")

    environ["QUERY_STRING"] = urlencode(remaining_query)

    return application(environ, start_response)
