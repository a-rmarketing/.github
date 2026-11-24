#!/usr/bin/env python3
"""Checks that run on every pull request. They run on GitHub, outside any AI session.

What they do, in plain words:
  1. Fail if a file looks like it holds a password or key.
  2. Fail if a file is too big for git, or is a source file that belongs in Google Drive.
  3. Fail if a link from one file in this repo to another points at nothing.
  4. Warn (not fail) when the pull request changes a rulebook file, so the reviewer reads it closely.

Changing this file, or the limits below, is a rulebook change. Never weaken a check to make it pass.
"""
import os
import re
import subprocess
import sys

MAX_BYTES = 2 * 1024 * 1024  # 2 MB. Bigger files go in Google Drive.

# Source files that belong in Google Drive, not git.
DRIVE_ONLY = {".psd", ".psb", ".ai", ".indd", ".sketch", ".fig", ".zip", ".mov", ".tif", ".tiff", ".raw", ".dmg"}

# File names that usually hold secrets.
SECRET_NAMES = re.compile(r"(^|/)(\.env(\..+)?|credentials\.json|.*\.pem|.*\.p12|id_rsa|id_ed25519)$")
SECRET_NAME_OK = re.compile(r"\.env\.(example|sample|template)$")

# Text that looks like a real key.
SECRET_TEXT = [
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("an API key", re.compile(r"\bsk-[A-Za-z0-9_-]{32,}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}\b")),
    ("a Netlify token", re.compile(r"\bnfp_[A-Za-z0-9]{36,}\b")),
]

# Files that set the rules. A change to any of these gets a warning for the reviewer.
RULEBOOK = re.compile(r"(^|/)(AGENTS\.md|CLAUDE\.md|facts\.md|voice\.md|CHANGES\.md)$|^\.github/|^\.claude/|(^|/)SKILL\.md$")

MD_LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def tracked_files():
    return [f for f in git("ls-files", "-z").split("\0") if f]


def changed_files():
    base = os.environ.get("BASE_SHA", "").strip()
    if not base:
        return []
    try:
        return [f for f in git("diff", "--name-only", "-z", f"{base}...HEAD").split("\0") if f]
    except subprocess.CalledProcessError:
        return []


def strip_code(text):
    """Blank out code blocks and inline code, so example links inside them aren't checked."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
        elif in_fence:
            out.append("")
        else:
            out.append(re.sub(r"`[^`]*`", "", line))
    return "\n".join(out)


def check_links(path, text, problems):
    for n, line in enumerate(strip_code(text).split("\n"), 1):
        for target in MD_LINK.findall(line):
            if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith(("#", "//")):
                continue  # a web link, an email link, or a jump within the same page
            if target.lower() in ("link", "url"):
                continue  # a placeholder in an example
            target = target.split("#")[0].split("?")[0].replace("%20", " ")
            if not target:
                continue
            if target.startswith("/"):
                dest = target.lstrip("/")
            else:
                dest = os.path.normpath(os.path.join(os.path.dirname(path), target))
            if not os.path.exists(dest):
                problems.append((path, n, f"Broken link: `{target}` doesn't exist in this repo."))


def main():
    problems, warnings = [], []

    for path in tracked_files():
        if not os.path.isfile(path):
            continue
        ext = os.path.splitext(path)[1].lower()
        size = os.path.getsize(path)

        if SECRET_NAMES.search(path) and not SECRET_NAME_OK.search(path):
            problems.append((path, 1, "This file name usually holds passwords or keys. Secrets never go in git."))
        if ext in DRIVE_ONLY:
            problems.append((path, 1, f"`{ext}` files are source files. They go in Google Drive, not git."))
        if size > MAX_BYTES:
            problems.append((path, 1, f"This file is {size / 1024 / 1024:.1f} MB. Files over 2 MB go in Google Drive, not git."))
            continue

        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (UnicodeDecodeError, OSError):
            continue  # not a text file (an image, a font)

        if path != ".github/scripts/pr_checks.py":
            for n, line in enumerate(text.split("\n"), 1):
                for label, pattern in SECRET_TEXT:
                    if pattern.search(line):
                        problems.append((path, n, f"This line looks like it holds {label}. Secrets never go in git."))
        if ext == ".md":
            check_links(path, text, problems)

    for path in changed_files():
        if RULEBOOK.search(path):
            warnings.append((path, "This pull request changes a rulebook file. Read every changed line before approving."))

    summary = []
    for path, line, msg in problems:
        print(f"::error file={path},line={line}::{msg}")
        summary.append(f"- ❌ `{path}` line {line}: {msg}")
    for path, msg in warnings:
        print(f"::warning file={path}::{msg}")
        summary.append(f"- ⚠️ `{path}`: {msg}")
    if not summary:
        summary.append("All checks passed: no secrets, no oversized or source files, no broken links, no rulebook changes.")

    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write("## Pull request checks\n\n" + "\n".join(summary) + "\n")
    else:
        print("\n".join(summary))

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
