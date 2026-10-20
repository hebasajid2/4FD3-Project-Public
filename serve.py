"""Run the whole app locally:  python serve.py [--port 8000] [--db nursing_system.db]

Serves the web app (public/) and the JSON API from one process, and can also
open the optional call-bell HTTP gateway for hardware/scripts (--gateway-port).
"""
import argparse
from wsgiref.simple_server import WSGIRequestHandler, make_server

from nursing_dss.bootstrap import create_system
from nursing_dss.web import create_app


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="SQLite file (default: nursing_system.db)")
    parser.add_argument("--no-seed", action="store_true", help="do not load demo data into an empty DB")
    parser.add_argument("--gateway-port", type=int, default=0,
                        help="also accept raw call-bell POSTs on this port (0 = off)")
    args = parser.parse_args()

    system = create_system(args.db, seed_if_empty=not args.no_seed)
    if args.gateway_port:
        from nursing_dss.input.http_gateway import serve_in_background
        serve_in_background(system, args.host, args.gateway_port)
        print(f"Call-bell gateway: POST http://{args.host}:{args.gateway_port}/bell")
    app = create_app(system, serve_static=True)
    print(f"Smart Nursing DSS running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    make_server(args.host, args.port, app, handler_class=QuietHandler).serve_forever()


if __name__ == "__main__":
    main()
