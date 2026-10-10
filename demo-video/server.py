#!/usr/bin/env python3
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from pathlib import Path
import json
ROOT=Path(__file__).resolve().parent
class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_POST(self):
        names={'/record/blog.webm':'blog.webm','/record/social.webm':'social.webm','/record/poster.jpg':'poster.jpg'}
        length=int(self.headers.get('Content-Length',0))
        if self.path not in names or not 0<length<160_000_000:
            self.send_error(400);return
        if self.headers.get('Origin') not in ('http://127.0.0.1:8788','http://localhost:8788'):
            self.send_error(403);return
        out=ROOT/'exports'/names[self.path];out.write_bytes(self.rfile.read(length))
        body=json.dumps({'file':out.name,'bytes':out.stat().st_size}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
ThreadingHTTPServer(('0.0.0.0',8788),Handler).serve_forever()
