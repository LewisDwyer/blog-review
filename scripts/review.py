#!/usr/bin/env python3
"""Dependency-free, local-only blog review server. Never modifies the source."""
import argparse
import copy
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import secrets
import threading
import tempfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, content):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def write_json(path, obj):
    atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def blocks(source):
    """Partition exact source spans, leaving separators untouched on composition."""
    result = []
    start = pos = 0
    fence = None
    frontmatter = source.startswith("---\n") or source.startswith("---\r\n")
    for number, line in enumerate(source.splitlines(keepends=True)):
        stripped = line.strip()
        if frontmatter:
            if number > 0 and stripped in ("---", "..."):
                frontmatter = False
        else:
            match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
            if match:
                marker = match.group(1)
                if fence is None:
                    fence = marker
                elif marker[0] == fence[0] and len(marker) >= len(fence) and not line.strip()[len(marker):].strip():
                    fence = None
            if not fence and not stripped:
                if source[start:pos].strip():
                    result.append({"id": f"b{len(result)+1}", "start": start, "end": pos,
                                   "source": source[start:pos]})
                start = pos + len(line)
        pos += len(line)
    if source[start:pos].strip():
        result.append({"id": f"b{len(result)+1}", "start": start, "end": pos, "source": source[start:pos]})
    return result


def inline(raw):
    # Escape first: neither MDX nor reviewer input can become executable HTML.
    value = html.escape(raw)
    tokens = []
    token_sources = []

    def stash(markup, source=None):
        tokens.append(markup)
        token_sources.append(source if source is not None else markup)
        return f"\x00{len(tokens)-1}\x00"

    def source_tokens(text):
        return re.sub(r"\x00(\d+)\x00", lambda m: source_tokens(token_sources[int(m[1])]), text)

    value = re.sub(r"`([^`\n]+)`", lambda m: stash("<code>" + m[1] + "</code>", m[0]), value)
    # Scan balanced destinations so parentheses in URLs and quoted titles survive.
    pattern = re.compile(r"(!?)\[([^\]\n]*)\]\(")
    cursor = 0
    rendered = []
    while match := pattern.search(value, cursor):
        depth, quote, end = 1, None, match.end()
        while end < len(value):
            char = value[end]
            if char == "\\":
                end += 2
                continue
            entity = next((q for q in ("&quot;", "&#x27;") if value.startswith(q, end)), None)
            if entity and (quote or (end > match.end() and value[end - 1].isspace())):
                quote = None if quote == entity else entity
                end += len(entity)
                continue
            if quote:
                end += 1
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            end += 1
        if depth:
            break
        rendered.append(value[cursor:match.start()])
        destination = source_tokens(value[match.end():end])
        if match[1]:
            markup = ('<span class="image-note" contenteditable="false" data-source="'
                      + html.escape(html.unescape(source_tokens(value[match.start():end + 1])), quote=True)
                      + '">Image: ' + (match[2] or destination) + '</span>')
        else:
            markup = '<span class="link-text" title="' + destination + '">' + match[2] + '</span>'
        rendered.append(stash(markup))
        cursor = end + 1
    value = "".join(rendered) + value[cursor:]
    value = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", value)
    # A link may contain an earlier code token. Resolve recursively, preserving
    # literal code contents and never interpreting them as Markdown a second time.
    def resolve(text):
        return re.sub(r"\x00(\d+)\x00", lambda m: resolve(tokens[int(m[1])]), text)
    return resolve(value)


def render(raw):
    stripped = raw.strip()
    if stripped.startswith(("```", "~~~")):
        lines = stripped.splitlines()
        end = -1 if len(lines) > 1 and re.match(r"^\s*(`{3,}|~{3,})\s*$", lines[-1]) else len(lines)
        return "<pre><code>" + html.escape("\n".join(lines[1:end])) + "</code></pre>"
    if stripped.startswith("---\n") or stripped.startswith("---\r\n") or stripped.startswith("<") or stripped.startswith("import ") or stripped.startswith("export "):
        return '<pre class="source-block">' + html.escape(stripped) + '</pre>'
    if re.fullmatch(r"[-*_]{3,}", stripped):
        return "<hr>"
    lines = raw.splitlines()
    list_pattern = re.compile(r"^((?:[-+*]|[0-9]+[.)])[ \t]+)(.+)$")
    # The small rich-text serializer supports flat, single-line lists only.
    # Retain all source for nesting, continuation lines, and indented code.
    has_list = any(re.match(r"^\s*(?:[-+*]|[0-9]+[.)])\s+", line) for line in lines)
    if (any(line.startswith(("    ", "\t")) for line in lines)
            or (has_list and any(line and (line[0].isspace() or not list_pattern.match(line)) for line in lines))):
        return '<pre class="source-block">' + html.escape(raw.rstrip("\r\n")) + '</pre>'
    output, para, items = [], [], []

    def flush():
        if para:
            output.append("<p>" + inline("\n".join(para)) + "</p>")
            para.clear()
        if items:
            kind = items[0][0]
            start = f' start="{int(re.match(r"[0-9]+", items[0][2])[0])}"' if kind == "ol" else ""
            output.append(f"<{kind}{start}>" + "".join('<li data-prefix="' + html.escape(x[2], quote=True) + '">' + inline(x[1]) + "</li>" for x in items) + f"</{kind}>")
            items.clear()

    for line in stripped.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.+)", line)
        item = list_pattern.match(line)
        if heading:
            flush()
            level = len(heading[1])
            output.append(f"<h{level}>" + inline(heading[2]) + f"</h{level}>")
        elif item:
            if para:
                flush()
            kind = "ul" if item[1][0] in "-+*" else "ol"
            if items and kind != items[0][0]:
                flush()
            items.append((kind, item[2], item[1]))
        elif line.startswith("> "):
            flush()
            output.append("<blockquote>" + inline(line[2:]) + "</blockquote>")
        else:
            if items:
                flush()
            para.append(line)
    flush()
    return "\n".join(output)


def compose(state):
    source = state["source"]
    for block in reversed(state["blocks"]):
        if block["id"] in state["edits"]:
            source = source[:block["start"]] + state["edits"][block["id"]] + source[block["end"]:]
    return source


def reconcile_submission(session, state):
    """Repair derived files from state.json, the sole authoritative commit."""
    if state["status"] != "submitted":
        for name in ("feedback.json", "submitted.md"):
            (session / name).unlink(missing_ok=True)
        return
    feedback_path = session / "feedback.json"
    try:
        feedback = json.loads(feedback_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        feedback = {}
    if any(feedback.get(key) != value for key, value in state.items()):
        feedback = copy.deepcopy(state)
        feedback["submitted_at"] = state["updated_at"]
        write_json(feedback_path, feedback)
    submitted = compose(state)
    path = session / "submitted.md"
    if not path.exists() or path.read_bytes() != submitted.encode("utf-8"):
        atomic(path, submitted)


def save_state(session, state):
    # Stage every byte before modifying published artifacts. Commit the authority
    # last, so failures never advance revision or lose the previous review.
    with tempfile.TemporaryDirectory(prefix=".save-", dir=session) as directory:
        stage = Path(directory)
        write_json(stage / "state.json", state)
        if state["status"] == "submitted":
            atomic(stage / "submitted.md", compose(state))
            feedback = copy.deepcopy(state)
            feedback["submitted_at"] = state["updated_at"]
            write_json(stage / "feedback.json", feedback)
            for name in ("submitted.md", "feedback.json"):
                os.replace(stage / name, session / name)
        else:
            for name in ("feedback.json", "submitted.md"):
                (session / name).unlink(missing_ok=True)
        os.replace(stage / "state.json", session / "state.json")


def public_state(state):
    result = copy.deepcopy(state)
    for block in result["blocks"]:
        block["html"] = render(result["edits"].get(block["id"], block["source"]))
    return result


def create_session(source_path, output=None):
    source_path = source_path.resolve()
    raw = source_path.read_bytes()
    source = raw.decode("utf-8")
    session = output or source_path.parent / ".blog-review" / (source_path.stem + "-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3))
    session = session.resolve()
    session.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(session.iterdir()):
        raise ValueError("Output directory is not empty. Use --resume to continue a review.")
    atomic(session / "source.md", source)
    atomic(session / ".token", secrets.token_urlsafe(32))
    state = {"schema_version": 1, "session_id": secrets.token_hex(12), "source_path": str(source_path),
             "source_sha256": digest(raw), "source": source, "blocks": blocks(source), "edits": {},
             "comments": [], "notes": "", "status": "draft", "revision": 0, "created_at": now(), "updated_at": now()}
    write_json(session / "state.json", state)
    return session


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, session, port):
        self.session = session
        self.state = json.loads((session / "state.json").read_text(encoding="utf-8"))
        reconcile_submission(session, self.state)
        self.token = (session / ".token").read_text().strip()
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, body, mime="application/json"):
        data = (json.dumps(body, ensure_ascii=False) if mime == "application/json" else body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed(self, auth=True):
        address = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != address:
            self.reply(403, {"error": "Invalid host"})
            return False
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + address:
            self.reply(403, {"error": "Invalid origin"})
            return False
        if auth and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            self.reply(401, {"error": "Open the full review link printed by the server, including its # access key."})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if not self.allowed(auth=path != "/"):
            return
        if path == "/":
            self.reply(200, (ASSETS / "review.html").read_text(), "text/html")
        elif path == "/api/state":
            with self.server.lock:
                try:
                    reconcile_submission(self.server.session, self.server.state)
                    self.reply(200, public_state(self.server.state))
                except OSError as exc:
                    self.reply(500, {"error": "Could not restore submission artifacts: " + str(exc)})
        else:
            self.reply(404, {"error": "Not found"})

    def do_POST(self):
        if not self.allowed():
            return
        path = urlsplit(self.path).path
        if path not in ("/api/save", "/api/render"):
            self.reply(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 10_000_000:
                raise ValueError("Review request is empty or too large")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a review object")
            if path == "/api/render":
                if not isinstance(payload.get("source"), str):
                    raise ValueError("Expected source text")
                self.reply(200, {"html": render(payload["source"])})
                return
            with self.server.lock:
                old = self.server.state
                if payload.get("revision") != old["revision"]:
                    self.reply(409, {"error": "This review changed in another tab. Copy any unsaved text, then reload to continue."})
                    return
                if not isinstance(payload.get("edits"), dict) or not isinstance(payload.get("comments"), list) or not isinstance(payload.get("notes"), str):
                    raise ValueError("Invalid review fields")
                ids = {b["id"] for b in old["blocks"]}
                if any(k not in ids or not isinstance(v, str) for k, v in payload["edits"].items()):
                    raise ValueError("Invalid edit")
                comment_ids = set()
                for c in payload["comments"]:
                    if not isinstance(c, dict) or c.get("block_id") not in ids or not all(isinstance(c.get(k), str) for k in ("id", "quote", "note", "context", "block_source_at_creation")) or not c["note"].strip():
                        raise ValueError("Invalid comment")
                    if c["id"] in comment_ids:
                        raise ValueError("Duplicate comment ID")
                    comment_ids.add(c["id"])
                if payload.get("status") not in ("draft", "submitted"):
                    raise ValueError("Invalid review status")
                state = copy.deepcopy(old)
                for k in ("edits", "comments", "notes", "status"):
                    state[k] = payload[k]
                state["revision"] += 1
                state["updated_at"] = now()
                session = self.server.session
                try:
                    save_state(session, state)
                except OSError:
                    # Best-effort repair also covers failure during replacement.
                    # Persistent disk failures are retried on read or resume.
                    try:
                        reconcile_submission(session, old)
                    except OSError:
                        pass
                    raise
                self.server.state = state
                self.reply(200, {"revision": state["revision"], "status": state["status"], "saved_at": state["updated_at"]})
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(400, {"error": str(exc)})
        except OSError as exc:
            self.reply(500, {"error": "Could not save review: " + str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("source", nargs="?", type=Path)
    serve.add_argument("--output", type=Path)
    serve.add_argument("--resume", type=Path)
    serve.add_argument("--port", type=int, default=0)
    status = sub.add_parser("status")
    status.add_argument("session", type=Path)
    args = parser.parse_args()
    if args.command == "status":
        state = json.loads((args.session / "state.json").read_text())
        reconcile_submission(args.session, state)
        print(json.dumps({k: state[k] for k in ("source_path", "status", "revision", "updated_at")}, indent=2))
        print("Submitted feedback:", (args.session / "feedback.json").exists())
        return
    if bool(args.source) == bool(args.resume) or (args.resume and args.output):
        parser.error("Provide a source path OR --resume SESSION (with no --output).")
    session = args.resume.resolve() if args.resume else create_session(args.source, args.output)
    # Hold an advisory lock so two server processes cannot write the same session.
    import fcntl
    lockfile = (session / ".server.lock").open("w")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error("This review session already has a running server.")
    server = ReviewServer(session, args.port)
    print(f"Review URL: http://127.0.0.1:{server.server_port}/#{server.token}", flush=True)
    print(f"Session: {session}\nFeedback: {session / 'feedback.json'}\nEdited draft: {session / 'submitted.md'}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        lockfile.close()


if __name__ == "__main__":
    main()
