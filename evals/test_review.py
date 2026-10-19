"""Run: python3 -m unittest discover -s <skill>/evals -v"""
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location("review", Path(__file__).parents[1] / "scripts/review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class RoundTrip(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "draft.mdx"
        self.raw = b'---\r\ntitle: Example\r\n---\r\n\r\n# Hello\r\n\r\nRepeat me.\r\n\r\nRepeat me.\r\n\r\n```python\r\nprint("hello")\r\n\r\nprint("world")\r\n```\r\n\r\n<Widget x="y" />\r\n'
        self.source.write_bytes(self.raw)
        self.session = review.create_session(self.source)
        self.server = review.ReviewServer(self.session, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, payload=None, token=True, **headers):
        if token:
            headers["Authorization"] = "Bearer " + self.server.token
        req = Request(self.url + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
        with urlopen(req) as response:
            return json.load(response)

    def payload(self):
        state = self.request("/api/state")
        return {k: state[k] for k in ("revision", "edits", "comments", "notes", "status")}

    def test_lossless_spans_and_fences(self):
        state = self.server.state
        self.assertEqual(review.compose(state).encode(), self.raw)
        self.assertEqual((self.session / "source.md").read_bytes(), self.raw)
        fences = [b for b in state["blocks"] if b["source"].startswith("```")]
        self.assertEqual(len(fences), 1)
        self.assertIn('\r\n\r\nprint', fences[0]["source"])

    def test_edit_comment_submit_reopen_and_resume(self):
        same = [b for b in self.server.state["blocks"] if b["source"].startswith("Repeat")]
        p = self.payload()
        p["edits"][same[1]["id"]] = "Second instance corrected.\r\n"
        p["comments"] = [{"id": "comment-1", "block_id": same[0]["id"], "quote": "Repeat", "note": "Explain why.", "context": "Repeat me.", "block_source_at_creation": same[0]["source"]}]
        p["notes"] = "Keep it concise."
        result = self.request("/api/save", p)
        self.assertFalse((self.session / "feedback.json").exists())
        p["revision"] = result["revision"]
        p["status"] = "submitted"
        result = self.request("/api/save", p)
        final = (self.session / "submitted.md").read_bytes()
        self.assertEqual(final.count(b"Repeat me."), 1)
        self.assertIn(b"Second instance corrected.", final)
        self.assertEqual(self.source.read_bytes(), self.raw)
        saved = json.loads((self.session / "feedback.json").read_text())
        self.assertEqual(saved["comments"][0]["note"], "Explain why.")
        self.assertEqual(saved["source_sha256"], review.digest(self.raw))
        # A new server object restores the persisted state.
        resumed = review.ReviewServer(self.session, 0)
        self.assertEqual(resumed.state["revision"], result["revision"])
        resumed.server_close()
        p["revision"] = result["revision"]
        p["status"] = "draft"
        self.request("/api/save", p)
        self.assertFalse((self.session / "feedback.json").exists())
        self.assertFalse((self.session / "submitted.md").exists())

    def test_submission_staging_failure_is_retryable(self):
        p = self.payload()
        p.update(status="submitted", notes="Do not lose these notes")
        original = review.write_json
        def fail_feedback(path, obj):
            if path.name == "feedback.json":
                raise OSError("injected feedback failure")
            return original(path, obj)
        with patch.object(review, "write_json", side_effect=fail_feedback):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/save", p)
            self.assertEqual(error.exception.code, 500)
            error.exception.close()
        self.assertEqual(self.server.state["revision"], 0)
        persisted = json.loads((self.session / "state.json").read_text())
        self.assertEqual(persisted["status"], "draft")
        self.assertFalse((self.session / "feedback.json").exists())
        self.assertFalse((self.session / "submitted.md").exists())
        result = self.request("/api/save", p)
        self.assertEqual(result["revision"], 1)
        self.assertEqual(json.loads((self.session / "feedback.json").read_text())["notes"], p["notes"])

    def test_failed_authority_commit_restores_previous_submission(self):
        p = self.payload()
        p.update(status="submitted", notes="Previously approved")
        result = self.request("/api/save", p)
        previous = (self.session / "submitted.md").read_bytes()
        p.update(revision=result["revision"], notes="Uncommitted changes")
        p["edits"][self.server.state["blocks"][1]["id"]] = "# Uncommitted heading\n"
        original = review.os.replace
        def fail_state(src, dst):
            if Path(dst) == self.session / "state.json":
                raise OSError("injected commit failure")
            return original(src, dst)
        with patch.object(review.os, "replace", side_effect=fail_state):
            with self.assertRaises(HTTPError) as error:
                self.request("/api/save", p)
            self.assertEqual(error.exception.code, 500)
            error.exception.close()
        self.assertEqual(self.server.state["revision"], result["revision"])
        self.assertEqual((self.session / "submitted.md").read_bytes(), previous)
        self.assertEqual(json.loads((self.session / "feedback.json").read_text())["notes"], "Previously approved")
        self.assertEqual(self.request("/api/save", p)["revision"], result["revision"] + 1)

    def test_resume_and_read_reconcile_artifacts(self):
        p = self.payload()
        p["status"] = "submitted"
        self.request("/api/save", p)
        (self.session / "feedback.json").write_text('{"revision": 999}')
        (self.session / "submitted.md").unlink()
        resumed = review.ReviewServer(self.session, 0)
        resumed.server_close()
        feedback = json.loads((self.session / "feedback.json").read_text())
        self.assertEqual(feedback["revision"], 1)
        self.assertEqual((self.session / "submitted.md").read_bytes(), self.raw)
        (self.session / "feedback.json").unlink()
        self.request("/api/state")
        self.assertTrue((self.session / "feedback.json").exists())
        p.update(revision=1, status="draft")
        self.request("/api/save", p)
        (self.session / "feedback.json").write_text('{"status":"submitted"}')
        self.request("/api/state")
        self.assertFalse((self.session / "feedback.json").exists())

    def test_stale_tab_cannot_overwrite(self):
        p = self.payload()
        self.request("/api/save", p)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/save", p)
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        self.assertEqual(self.server.state["revision"], 1)

    def test_token_origin_and_script_injection(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/state", token=False)
        self.assertEqual(error.exception.code, 401)
        error.exception.close()
        with self.assertRaises(HTTPError) as error:
            self.request("/api/save", self.payload(), Origin="https://example.com")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        rendered = self.request("/api/render", {"source": '<script>alert(1)</script>\n\n[link](javascript:alert(1))'})["html"]
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("href=", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_invalid_comment_does_not_corrupt_review(self):
        p = self.payload()
        p["comments"] = [{"block_id": "unknown"}]
        with self.assertRaises(HTTPError) as error:
            self.request("/api/save", p)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.server.state["revision"], 0)


class Rendering(unittest.TestCase):
    def test_simple_lists_keep_prefix_and_start(self):
        rendered = review.render("7)  Seven\n8) Eight\n")
        self.assertIn('<ol start="7">', rendered)
        self.assertIn('<li data-prefix="7)  ">Seven</li>', rendered)
        self.assertIn('<li data-prefix="8) ">Eight</li>', rendered)
        self.assertIn('<li data-prefix="+  ">First</li>', review.render("+  First\n* Second"))

    def test_unsupported_list_structure_and_indented_code_stay_source(self):
        for source in ("- Parent\n  - Child\n- Next", "1. Parent\n   continuation\n2. Next",
                       "- Parent\ncontinuation\n- Next", "    print('hello')\n    print('world')"):
            with self.subTest(source=source):
                rendered = review.render(source)
                self.assertTrue(rendered.startswith('<pre class="source-block">'))
                self.assertIn(review.html.escape(source), rendered)
                self.assertNotIn("<li", rendered)

    def test_nested_inline_code_in_link(self):
        rendered = review.inline("Read [the `API`](https://example.com) today.")
        self.assertNotIn("\x00", rendered)
        self.assertEqual(rendered, 'Read <span class="link-text" title="https://example.com">the <code>API</code></span> today.')

    def test_balanced_links_and_images_with_titles(self):
        rendered = review.inline('[ref](https://example.com/a_(b))')
        self.assertEqual(rendered, '<span class="link-text" title="https://example.com/a_(b)">ref</span>')
        source = '![alt `code`](images/a_(b).png "Title (unfinished")'
        rendered = review.inline(source)
        self.assertIn('data-source="' + review.html.escape(source, quote=True) + '"', rendered)
        self.assertTrue(rendered.endswith('</span>'))
        self.assertNotIn("\x00", rendered)


if __name__ == "__main__":
    unittest.main()
