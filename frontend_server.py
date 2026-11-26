from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


FRONTEND_PATH = Path(__file__).with_name("frontend")


class FrontendHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in {"/product/cotton", "/product/cotton/"}:
            query = self.path.partition("?")[2]
            self.path = "/product.html"
            if query:
                self.path += f"?{query}"
        super().do_GET()


def main():
    handler = partial(FrontendHandler, directory=str(FRONTEND_PATH))
    server = ThreadingHTTPServer(("127.0.0.1", 5500), handler)
    print("Frontend running at http://127.0.0.1:5500")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
