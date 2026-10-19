---
name: blog-review
description: Give a blog author an interactive local HTML page that feels like editing a physical draft, with full-page or paragraph-by-paragraph focus modes, text editable by default, highlighting, cross-outs, margin notes, autosave, and submitted feedback that the agent implements in the original Markdown or MDX. Use whenever a user wants to mark up a blog, annotate a draft, review an article in a browser, focus on one paragraph at a time, or apply feedback collected by this skill. When explicitly asked to use this workflow for blog writing, prepare a review once the draft is ready.
---

# Blog review

Turn a draft into a browser review, then implement the author's submitted feedback. Use the bundled server and page rather than rebuilding an editor on each invocation. Requires Python 3.9+ on macOS or Linux and a modern browser; no external services or packages are needed.

## Prepare the review

1. Identify the current draft from the conversation and project instructions. Use the actual Markdown or MDX source, not a deployed URL. Ask for a path only when the draft cannot be identified. Finish any already requested drafting work first.
2. Start the server as a persistent terminal process:

   ```sh
   python3 <skill-directory>/scripts/review.py serve /absolute/path/to/draft.md
   ```

   Use an execution tool that returns a running session; leave that process alive during review. The server binds only to `127.0.0.1` on an available port. Its output gives the full review URL (including its access fragment), session directory, and submission paths. Preserve that information in your task notes. Do not expose the access URL publicly.
3. Open that URL in the user's browser if browser access is available, and give the user a clickable localhost link. The HTML is self-contained and has no remote assets. If the user's browser runs on a different machine, explain that localhost must be forwarded or run the script on their machine; do not claim a remote localhost URL is accessible.
4. Explain the controls briefly: click anywhere on the page and type to make corrections. Select words to show a floating toolbar with **Highlight**, **Cross out**, and **Add note**. Marks stay visible on the page; **Add note** immediately creates and focuses an editable margin note that saves as you type. The margin **+** adds a note on a whole passage. Use Cmd/Ctrl+Alt+M to comment on a selection. Leave overall notes in the sidebar, then click **Submit review**. Saving occurs in the background; submission waits for it to finish. The browser confirms success only after the files have been written. Nothing changes the source draft yet.
5. Yield for review. Tell the user to return and say “apply my feedback” when finished. Submission writes files; it does not wake or message an idle agent. If the environment supports an explicit continuation mechanism, it can read the submitted file, but do not promise automatic agent execution.

All text is editable directly in the reading view, with normal browser undo/redo. Rich text edits serialize to Markdown only in changed passages; untouched source stays byte-for-byte intact. Frontmatter, custom MDX, and unsupported complex Markdown (such as nested or continued lists) appear as editable source to preserve their meaning. Simple lists retain their markers and numbering; code fences retain their language. Links and image targets survive adjacent edits. The preview approximates Markdown rather than executing MDX. Relative images are descriptive placeholders. Selection marks use the modern browser CSS Highlight API, so they remain separate from article text and do not alter its Markdown. Notes, general feedback, export, and reopening after submission are also available.

### Paragraph focus mode

Use the **Full page / Paragraph focus** toggle above the draft. Full page is the default. Focus mode keeps the entire page in place and blurs every passage except the current one. Click any blurred passage to clear it and begin editing there; Previous and Next are optional shortcuts, with a sticky position indicator. Keyboard users can tab to a blurred passage and press Enter or Space to focus it. Ordinary paragraphs are separate passages; headings, metadata, lists, code, and components remain available too. Only notes affecting the current passage appear in the sidebar. Overall feedback remains available.

The same live editors and feedback data serve both views. Switching views preserves edits, page position, and undo history; returning to full page clears all blur and reveals all notes. Clicking another passage or using Next/Previous saves outstanding changes before changing focus and stays on the current passage if saving fails. Clicking a passage preserves the scroll position and places the caret near the click; Next/Previous scroll to their destination. Cmd/Ctrl+Alt+Left/Right also navigate in focus mode. The browser remembers the mode and current passage locally for each review session. Reaching the last passage does not submit automatically: use **Submit review** when ready. A focus position is navigation, not proof that earlier passages have been approved.

Session data defaults to a `.blog-review/` directory beside the source. Do not commit this directory or its local access token. If project conventions require artifacts elsewhere, pass `--output /absolute/session-directory`. Avoid altering repository ignore rules without need.

Resume a stopped server without losing review data:

```sh
python3 <skill-directory>/scripts/review.py serve --resume /absolute/session-directory
```

## Implement submitted feedback

1. Read `state.json`, `feedback.json`, `source.md`, and `submitted.md` in the session directory. Before applying anything, verify that state and feedback both carry `status: submitted` and the same session ID, revision, edits, comments, and notes; verify that `submitted.md` equals the exact source composed with those edits. A draft, missing artifact, or mismatched revision is not a submitted review. Resume the server to recover incomplete artifacts from authoritative state if needed. Feedback contains the original source path and SHA-256 digest, original blocks and offsets, direct edits, original and reviewed selection anchors, selected quotes and surrounding context, and general notes. `submitted.md` incorporates literal edits but does not interpret comments. Reopening a review withdraws submission until it is submitted again.
2. Compare the current source's SHA-256 with `source_sha256`. If it matches, start from `submitted.md` and implement every comment and general note. Explicit edits are the author's intended wording. Change them further only where another instruction requires it. For comments made before or after a block edit, read both the original and reviewed text plus context; retain the comment's meaning even when its quote has moved. Do not silently drop comments just because exact string matching fails.
   Interpret `kind: delete` as a physical cross-out: remove the selected words unless its accompanying note requests a replacement or another treatment. `kind: highlight` identifies a passage for attention; a highlight alone (`Highlighted passage.`) requests no wording change. Apply its note when present. `kind: comment` (or absent kind in earlier reviews) is a margin note; `No note added.` means an empty note and requests no change. Comments spanning multiple passages include an `anchors` array. Original `quote`, offsets, context, and source at creation remain intact. The optional `current_quote`, `current_start`, `current_end`, and `current_context` track that mark through direct edits; use these reviewed anchors to locate its current wording. `removed_in_review` means the reviewer already deleted that text; do not delete a matching phrase elsewhere. `detached` means the location is uncertain: inspect context without guessing at repeated phrases. Marks are reviewer instructions, not markup to insert into the final article.
3. If the source changed since review creation, inspect the source diff and merge the feedback into the current file. Do not replace newer work with the snapshot. Repeated passages require block and context matching rather than a global replacement. Resolve routine wording choices yourself; ask a targeted question only for an actual conflict or ambiguity that cannot be safely resolved.
4. Preserve frontmatter, links, code examples, MDX components, and unrelated edits. Follow applicable writing/style skills and repository instructions. Verify factual changes using the tools appropriate to the claim; reviewer notes can request a claim, but are not evidence for it.
5. Save the actual draft and run checks appropriate to the edits. Read the diff to ensure changes were applied to the intended passages. Write `applied.json` in the session directory with the submitted revision, final source SHA-256, timestamp, and an outcome for each comment plus general notes. This records what was applied, not an irreversible action or publication. If that submitted revision is already applied, inspect the receipt and source before repeating work.
6. Report the updated source link and a brief account of the changes and anything still unresolved. Do not publish or send notifications unless authorized. If the user wants another review round, create a new session from the revised draft so its anchors and hash reflect the new source.

## Saved files

- `source.md`: exact original source; never rewritten by review actions.
- `state.json`: latest saved draft review, with a revision counter.
- `feedback.json`: submitted review, including direct edits, comments, and overall notes.
- `submitted.md`: original source with the reviewer's literal block edits.
- `applied.json`: agent-written receipt after implementing submitted feedback.
- `.token`: local access token; keep private.

Saving uses atomic replacement. Revision checks prevent two browser tabs from silently overwriting one another; a stale tab must reload. The server never writes the source draft. To inspect submission status without a browser:

```sh
python3 <skill-directory>/scripts/review.py status /absolute/session-directory
```
