# Blog review

Give an author a local browser page to mark up a Markdown or MDX draft, then apply their feedback to the original file. The page supports direct editing, yellow highlights, cross-outs, margin notes, and overall feedback.

**Paragraph focus** keeps the entire page visible while blurring surrounding passages. Click any blurred passage to focus and edit it, or use the optional Previous/Next controls. Edits save before focus changes; a failed save keeps the current passage and its text in place.

## Install

Clone the repository into your agent’s skills directory, keeping `SKILL.md`, `scripts/`, and `assets/` together. For example:

```sh
mkdir -p ~/.agents/skills
git clone https://github.com/LewisDwyer/blog-review.git ~/.agents/skills/blog-review
```

Or clone it anywhere and ask your agent to read its `SKILL.md` directly and follow it without installing.

## Requirements

- Python 3.9+ on macOS or Linux.
- A modern browser with the CSS Highlight API.
- An agent that can run local commands and edit files.

There are no Python packages, API keys, or external services to configure. Browser regression tests use Playwright separately; it is not required to run the skill.

## Use

Ask your agent:

> Use blog-review on /absolute/path/to/post.md.

The agent starts a local review server and gives you the complete access URL. Open it to edit the draft:

- Click into the text to make a correction.
- Select words for **Highlight**, **Cross out**, or **Add note**.
- Switch between **Full page** and **Paragraph focus** above the draft.
- Write in the margin notes or the overall feedback box; changes save as you type.
- Click **Submit review**, then tell the agent **Apply my feedback**.

Submission writes feedback files; it does not wake an idle agent. The agent implements literal edits and notes, removes crossed-out text, checks for newer changes in the source, and records an application receipt. A highlight by itself requests no wording change. Publishing is a separate action.

## Run manually

From this repository’s root:

```sh
python3 scripts/review.py serve /absolute/path/to/post.md
```

The server prints its localhost URL and review-session directory. Keep the complete URL, including its access fragment. The server binds only to `127.0.0.1`; the browser must be on the same machine, or localhost must be forwarded.

Resume an existing session:

```sh
python3 scripts/review.py serve --resume /absolute/path/to/session
```

Inspect submission status:

```sh
python3 scripts/review.py status /absolute/path/to/session
```

## Saved feedback

Sessions default to `.blog-review/` beside the source; `--output /absolute/session-directory` chooses another location. Keep review sessions and local access tokens out of commits. The server preserves an exact source snapshot and never writes the original draft.

- `source.md`: original source snapshot.
- `state.json`: authoritative saved review, with status and revision.
- `feedback.json` and `submitted.md`: submitted instructions and literal corrections.
- `applied.json`: agent-written record of implemented feedback.

The applying agent verifies that submitted files agree with authoritative state before using them. Reopening a review withdraws its submission until you submit again.

## Rendering limits

The page approximates Markdown rather than executing MDX. Frontmatter, custom components, and unsupported complex Markdown such as nested lists appear as editable source to preserve their meaning. Relative images use descriptive placeholders. Untouched source stays byte-for-byte intact.

## Checks

Run the dependency-free server and renderer tests from the repository root:

```sh
python3 -m unittest discover -s evals -v
```

The browser regressions use Node.js and a Playwright installation with Chromium available. If Playwright is already resolvable by Node:

```sh
node evals/browser_regression.cjs
node evals/browser_focus.cjs
```

Otherwise, point `BLOG_REVIEW_PLAYWRIGHT` at its installed package directory, for example:

```sh
BLOG_REVIEW_PLAYWRIGHT=/path/to/node_modules/playwright node evals/browser_focus.cjs
```

Each browser check creates a temporary draft, starts its own server on an available port, exercises the UI, and stops the server afterward. Screenshots stay in the temporary fixture directory. Tests cover source fidelity, annotation anchors, save/retry/conflict handling, click-to-focus, navigation, notes, mobile layout, and submission.

## Origin

Developed for [Ritza Skills](https://github.com/ritza-co/skills/tree/main/blog-review). This repository provides the skill as a standalone installation.
