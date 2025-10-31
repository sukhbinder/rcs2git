#!/usr/bin/env python3
"""
rcs2git.py

Convert RCS `*,v` files to a git fast-import stream using the RCS command-line tools (rlog, co).

Usage:
    python3 rcs2git.py [options] paths...

Example:
    git init destrepo
    cd destrepo
    python3 rcs2git.py /path/to/rcs_project --authors-file authors.txt | git fast-import
    git reset

This aims to provide feature parity (practical) with the Ruby rcs-fast-export.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
import os
import fnmatch
import time
import datetime
import shlex
from typing import List, Dict, Tuple, Optional, Set

# ---------- Utilities ----------





def safe_split_lines(s: str) -> List[str]:
    return s.splitlines()


def parse_rcs_date(s: str) -> int:
    """
    RCS date format (common): YYYY.MM.DD.HH.MM.SS
    Some old files might have 2-digit year - treat < 3 digits as 19xx.
    Returns unix timestamp (int).
    """
    parts = s.strip().split(".")
    if len(parts) < 6:
        # fallback: try to parse common RFC-ish date fragments
        try:
            dt = datetime.datetime.fromisoformat(s)
            return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())
        except Exception:
            return int(time.time())
    y = parts[0]
    if len(y) < 3:
        y = "19" + y  # follow Ruby script behavior
    try:
        parts_i = list(map(int, [y] + parts[1:6]))
        dt = datetime.datetime(
            parts_i[0],
            parts_i[1],
            parts_i[2],
            parts_i[3],
            parts_i[4],
            parts_i[5],
            tzinfo=datetime.timezone.utc,
        )
        return int(dt.timestamp())
    except Exception:
        return int(time.time())


def load_authors_file(fn: str, warn_missing: bool) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    try:
        with open(os.path.expanduser(fn), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    uname, author = line.split("=", 1)
                    uname = uname.strip()
                    author = author.strip()
                    if uname in mapping:
                        sys.stderr.write(
                            f"Warning: username {uname} redefined to {author}\n"
                        )
                    mapping[uname] = author
    except FileNotFoundError:
        sys.stderr.write(f"Warning: authors file {fn} not found\n")
    return mapping


# ---------- RCS parsing using rlog/co ----------


class Revision:
    def __init__(self, rev: str):
        self.rev = rev
        self.date_iso: Optional[str] = None
        self.date_ts: Optional[int] = None
        self.author: Optional[str] = None
        self.state: Optional[str] = None
        self.branches: List[str] = []
        self.symbols: Set[str] = set()
        self.log: str = ""
        self.has_log_started: bool = False

    def __repr__(self):
        return f"<Revision {self.rev} author={self.author} date={self.date_iso} symbols={sorted(self.symbols)}>"


class FileHistory:
    def __init__(self, rcs_path: str, filename: str):
        self.rcs_path = rcs_path  # path to the ,v file
        self.filename = filename  # logical filename used in git (path minus ,v)
        self.symbols: Dict[str, str] = {}  # symbol -> revision (from header)
        self.revisions: List[Revision] = (
            []
        )  # sorted newest->oldest as returned by rlog; we'll reverse later

    def __repr__(self):
        return f"<FileHistory {self.filename} revs={len(self.revisions)} symbols={list(self.symbols.keys())}>"


def parse_rlog(rcs_path: str, log_encoding: Optional[str] = None) -> FileHistory:
    """
    Uses `rlog -z` to obtain revision list, symbol table and logs.
    Returns a FileHistory object.
    """
    # -z produces NUL at the end? On some systems -z changes separators.
    # We'll use plain rlog and parse reasonably robustly.
    # We'll try "rlog -z" first (common), fallback to "rlog" if it errors.
    cmd = ["rlog", "-z", rcs_path]
    try:
        result = subprocess.run(["rlog", "-z", rcs_path], capture_output=True, text=True, check=True)
        out = result.stdout
    except subprocess.CalledProcessError:
        result = subprocess.run(["rlog", rcs_path], capture_output=True, text=True, check=True)
        out = result.stdout

    if log_encoding:
        try:
            out = out.encode("latin1").decode(log_encoding)
        except Exception:
            # best-effort: leave as-is
            pass

    lines = out.splitlines()
    fh = FileHistory(rcs_path, os.path.basename(rcs_path).replace(",v", ""))
    cur_rev: Optional[Revision] = None
    in_header_section = True
    # rlog top-level may include "symbols:" or "symbol:" lines prior to revisions;
    # older rlog prints "symbol: NAME:rev; ..." possibly over multiple lines.
    # We'll do a simple parse: look for lines starting with "symbol" or "symbols"
    i = 0
    while i < len(lines):
        line = lines[i]
        # header/symbols (occur before revisions)
        if in_header_section:
            stripped = line.strip()
            if (
                stripped.startswith("symbols:")
                or stripped.startswith("symbol:")
                or stripped.startswith("symbol")
            ):
                # collect the rest until a blank or "revision"
                # example: "symbol: RELEASE_1_0:1.2; RELENG:1.1;"
                parts = stripped.split(":", 1)
                if len(parts) > 1:
                    rest = parts[1]
                else:
                    rest = ""
                # Sometimes symbols are split across multiple lines; accumulate until a line with 'revision' occurs
                j = i + 1
                while (
                    ";" in rest
                    and not rest.strip().endswith(";")
                    and j < len(lines)
                    and not lines[j].strip().startswith("revision")
                ):
                    rest += " " + lines[j].strip()
                    j += 1
                # parse name:rev; pairs
                for pair in rest.strip().split(";"):
                    pair = pair.strip()
                    if not pair:
                        continue
                    if ":" in pair:
                        name, rev = pair.split(":", 1)
                        fh.symbols[name.strip()] = rev.strip()
                # fallthrough; continue parse
            if line.strip().startswith("revision "):
                in_header_section = False
                # continue to revision handling
            else:
                i += 1
                continue

        # revision blocks
        if line.strip().startswith("revision "):
            if cur_rev:
                fh.revisions.append(cur_rev)
            revid = line.strip().split()[1]
            cur_rev = Revision(revid)
            # read following header lines for this revision
            i += 1
            while i < len(lines):
                l = lines[i]
                ls = l.strip()
                if ls.startswith("date:"):
                    # expected "date: 1999.01.01.12.00.00;  author: joe;  state: Exp;"
                    # parts separated by ';'
                    parts = ls.split(";")
                    for p in parts:
                        p = p.strip()
                        if p.startswith("date:"):
                            val = p.split("date:", 1)[1].strip()
                            cur_rev.date_iso = val
                            cur_rev.date_ts = parse_rcs_date(val)
                        elif p.startswith("author:"):
                            cur_rev.author = p.split("author:", 1)[1].strip()
                        elif p.startswith("state:"):
                            cur_rev.state = p.split("state:", 1)[1].strip()
                        elif p.startswith("branches:"):
                            # branches may be blank or have values
                            bval = p.split("branches:", 1)[1].strip()
                            if bval:
                                cur_rev.branches = [x for x in bval.split() if x]
                    i += 1
                    continue
                if ls.startswith("branches:"):
                    # alternative branches line
                    b = ls.split("branches:", 1)[1].strip()
                    if b:
                        cur_rev.branches = [x for x in b.split() if x]
                    i += 1
                    continue
                if ls.startswith("symbols:") or ls.startswith("symbol:"):
                    # sometimes per-revision symbols; treat them as symbols for that revision
                    rest = ls.split(":", 1)[1] if ":" in ls else ""
                    j = i + 1
                    while (
                        ";" in rest
                        and not rest.strip().endswith(";")
                        and j < len(lines)
                        and not lines[j].strip().startswith("log")
                    ):
                        rest += " " + lines[j].strip()
                        j += 1
                    for pair in rest.split(";"):
                        pair = pair.strip()
                        if not pair:
                            continue
                        if ":" in pair:
                            name, rev2 = pair.split(":", 1)
                            cur_rev.symbols.add(name.strip())
                    i += 1
                    continue
                if ls == "log":
                    # next lines until a line ending with '@' (or blank line) are the log content
                    # rlog prints logs between lines starting with '@' in RCS; but how rlog prints logs is variable.
                    # We'll read subsequent lines until a line 'text' or '----------------' or blank after '@' indicators.
                    # Simple approach: collect until a line "---" or until we see 'next' or 'text' or 'revision'
                    i += 1
                    log_lines = []
                    while (
                        i < len(lines)
                        and not lines[i].strip().startswith("text")
                        and not lines[i].strip().startswith("revision")
                    ):
                        log_lines.append(lines[i])
                        i += 1
                    cur_rev.log = "\n".join(log_lines).rstrip()
                    continue
                # termination of revision header: rlog often inserts a line of dashes or blank line before next revision or text
                if (
                    ls == ""
                    or ls.startswith("-----")
                    or ls.startswith("=====")
                    or ls.startswith("text")
                ):
                    i += 1
                    break
                i += 1
        else:
            i += 1

    if cur_rev:
        fh.revisions.append(cur_rev)

    # rlog returns revisions newest first; reverse to chronological order (oldest first)
    fh.revisions = list(reversed(fh.revisions))
    # incorporate file-level symbols parsed earlier
    for name, rev in fh.symbols.items():
        # mark symbol on revision if present
        for r in fh.revisions:
            if r.rev == rev:
                r.symbols.add(name)
                break
    return fh


def get_revision_content(rcs_path: str, rev: str, expand_keywords: bool = False) -> str:
    """
    Use `co -q -pREV` to print the full text of a revision.
    expand_keywords currently ignored (co will expand by default if keywords are present).
    """
    cmd = ["co", "-q", f"-p{rev}", rcs_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


# ---------- Build commit objects ----------


class SingleFileCommit:
    def __init__(self, filename: str, rcs_path: str, rev: Revision, content: str):
        self.filename = filename  # path in repo
        self.rcs_path = rcs_path  # path to the ,v file
        self.rev_id = rev.rev
        self.author = rev.author or "unknown"
        self.date_ts = rev.date_ts or int(time.time())
        self.log = rev.log or ""
        self.symbols = set(rev.symbols)
        self.branches = list(rev.branches)
        self.content = content

    def __repr__(self):
        return f"<SFC {self.filename}@{self.rev_id} author={self.author} date={self.date_ts}>"


# ---------- Fast-import emission ----------


class FastImportEmitter:
    def __init__(
        self,
        author_map: Dict[str, str],
        author_is_committer: bool,
        tag_each_rev: bool,
        log_filename: bool,
    ):
        self.author_map = author_map
        self.author_is_committer = author_is_committer
        self.tag_each_rev = tag_each_rev
        self.log_filename = log_filename
        self._next_mark = 1
        self._blob_marks: Dict[Tuple[str, str], int] = {}  # (filename,rev) -> mark
        self._commit_marks: Dict[int, int] = (
            {}
        )  # sequential index -> mark (we'll use list-index)
        self._last_commit_mark_for_file: Dict[str, int] = {}
        self._printed_blobs: Set[int] = set()

    def _alloc_mark(self) -> int:
        m = self._next_mark
        self._next_mark += 1
        return m

    def author_for(self, username: str) -> str:
        if username in self.author_map:
            return self.author_map[username]
        # default mapping
        self.author_map[username] = f"{username} <{username}@example.com>"
        return self.author_map[username]

    def emit_blob(self, filename: str, rev_id: str, content: str, mode: str = "644"):
        key = (filename, rev_id)
        if key in self._blob_marks:
            return self._blob_marks[key]
        bmark = self._alloc_mark()
        self._blob_marks[key] = bmark
        # print blob entry now
        # fast-import expects: blob\nmark :N\ndata <len>\n<content>
        # We will print as raw; ensure content length counts bytes (encode utf-8)
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        sys.stdout.write(f"blob\nmark :{bmark}\ndata {len(content_bytes)}\n")
        sys.stdout.buffer.write(content_bytes)
        sys.stdout.buffer.write(b"\n")
        return bmark

    def emit_commit(
        self,
        tree_entries: List[Tuple[str, int, str]],
        author_name: str,
        date_ts: int,
        logmsg: str,
        parent_mark: Optional[int],
        commit_tag_symbols: List[str] = [],
        commit_index: Optional[int] = None,
    ):
        """
        tree_entries: list of tuples (path, blob_mark, mode) where mode like '644' or '755' or 'D' for delete
        parent_mark: optional parent commit mark to set "from :N"
        commit_tag_symbols: list of tags to set pointing to this commit
        """
        commit_mark = self._alloc_mark()
        # output commit
        branch = "master"
        au = self.author_for(author_name)
        date_str = f"{int(date_ts)} +0000"
        sys.stdout.write(f"commit refs/heads/{branch}\n")
        sys.stdout.write(f"mark :{commit_mark}\n")
        sys.stdout.write(f"author {au} {date_str}\n")
        if self.author_is_committer:
            sys.stdout.write(f"committer {au} {date_str}\n")
        else:
            # use current environment committer identity
            try:
                committer = subprocess.check_output(
                    ["git", "var", "GIT_COMMITTER_IDENT"], text=True
                ).strip()
            except Exception:
                committer = au + " " + date_str
            sys.stdout.write(f"committer {committer}\n")
        if logmsg is None:
            logmsg = ""
        sys.stdout.write(f"data {len(logmsg.encode('utf-8'))}\n")
        if logmsg:
            sys.stdout.write(logmsg + "\n")
        if parent_mark:
            sys.stdout.write(f"from :{parent_mark}\n")
        # write tree entries
        for path, bmark, mode in tree_entries:
            if bmark is None:
                # deletion
                sys.stdout.write(f"D {path}\n")
            else:
                sys.stdout.write(f"M {mode} :{bmark} {path}\n")
        # tags pointing to this commit (reset refs/tags/NAME)
        for t in commit_tag_symbols:
            sys.stdout.write(f"reset refs/tags/{t}\n")
            sys.stdout.write(f"from :{commit_mark}\n")
        # potentially also tag each rev
        sys.stdout.write("\n")
        # update last commit mark mapping for files in this commit
        for path, bmark, mode in tree_entries:
            if bmark is not None:
                self._last_commit_mark_for_file[path] = commit_mark
        return commit_mark


# ---------- High-level flow ----------


def collect_histories(
    paths: List[str],
    ignore_patterns: List[str],
    log_encoding: Optional[str],
    expand_keywords: bool,
    skip_branches: bool,
) -> List[FileHistory]:
    histories: List[FileHistory] = []
    for path in paths:
        if os.path.isdir(path):
            # walk and find files ending with ,v
            for root, _, files in os.walk(path):
                for f in files:
                    if not f.endswith(",v"):
                        continue
                    relpath = os.path.join(root, f)
                    # compute logical filename relative to passed path (strip leading path portion)
                    # We'll keep the filename relative to the provided path base
                    # Build logical filename preserving subdirs under the input dir
                    # For simplicity, use path relative to input root
                    # But if user provided multiple paths, we keep the basename path under repo root.
                    # We'll just use path relative to the passed directory argument
                    should_ignore = False
                    for pat in ignore_patterns:
                        if fnmatch.fnmatch(relpath, pat) or fnmatch.fnmatch(
                            os.path.basename(relpath), pat
                        ):
                            should_ignore = True
                            break
                    if should_ignore:
                        continue
                    try:
                        fh = parse_rlog(relpath, log_encoding)
                    except Exception as e:
                        sys.stderr.write(f"Failed to parse {relpath}: {e}\n")
                        continue
                    # if skip_branches option, filter out revisions that are branch-only?
                    if skip_branches:
                        # remove revisions that have revision.branch indications in their branches field
                        fh.revisions = [r for r in fh.revisions if not r.branches]
                    histories.append(fh)
        else:
            # single file
            if not path.endswith(",v"):
                sys.stderr.write(f"Skipping {path} (not an RCS ,v file)\n")
                continue
            should_ignore = False
            for pat in ignore_patterns:
                if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(
                    os.path.basename(path), pat
                ):
                    should_ignore = True
                    break
            if should_ignore:
                continue
            try:
                fh = parse_rlog(path, log_encoding)
            except Exception as e:
                sys.stderr.write(f"Failed to parse {path}: {e}\n")
                continue
            histories.append(fh)
    return histories


def build_single_file_commits(
    histories: List[FileHistory], expand_keywords: bool
) -> List[SingleFileCommit]:
    sfcs: List[SingleFileCommit] = []
    for fh in histories:
        for rev in fh.revisions:
            # skip revision if no date or no author? we'll include but default values assigned
            try:
                content = get_revision_content(fh.rcs_path, rev.rev, expand_keywords)
            except Exception as e:
                sys.stderr.write(
                    f"Warning: co failed for {fh.rcs_path} rev {rev.rev}: {e}\n"
                )
                content = ""
            sfc = SingleFileCommit(fh.filename, fh.rcs_path, rev, content)
            sfcs.append(sfc)
    # sort by timestamp ascending (oldest first)
    sfcs.sort(key=lambda s: s.date_ts)
    return sfcs


def coalesce_commits(
    single_commits: List[SingleFileCommit],
    commit_fuzz: int,
    symbol_check: bool,
    warn_missing_authors: bool,
) -> List[Dict]:
    """
    Coalesce single-file commits into multi-file commits where appropriate.
    Returns list of commit dicts:
        { 'date_ts': int, 'author': str, 'log': str, 'files': [SingleFileCommit], 'symbols': set() }
    """
    commits: List[Dict] = []
    # We'll perform a forward scan similar to Ruby but simpler:
    i = 0
    n = len(single_commits)
    while i < n:
        base = single_commits[i]
        group_files = [base]
        group_symbols = set(base.symbols)
        j = i + 1
        while j < n:
            cand = single_commits[j]
            # stop if too far in time
            if cand.date_ts > base.date_ts + commit_fuzz:
                break
            # require same author & same log
            if cand.author != base.author or cand.log != base.log:
                j += 1
                continue
            # require symbol subset condition or symbol_check disabled
            if symbol_check:
                # allow coalescing if symbols are subset in one direction
                if not (
                    group_symbols.issubset(cand.symbols)
                    or cand.symbols.issubset(group_symbols)
                ):
                    # do not coalesce
                    j += 1
                    continue
            # ensure no duplicate filenames in the group (we don't want two revisions of same file in one commit)
            filenames = {f.filename for f in group_files}
            if cand.filename in filenames:
                j += 1
                continue
            # good to merge
            group_files.append(cand)
            group_symbols |= cand.symbols
            j += 1
        # build commit record
        commit = {
            "date_ts": base.date_ts,
            "author": base.author,
            "log": base.log,
            "files": group_files,
            "symbols": group_symbols,
        }
        commits.append(commit)
        # advance i to the next non-merged index
        i = i + 1
        # skip over any that we included beyond i (we included group_files from i..j-1)
        # but because we built group by scanning, we should skip all files that are in group_files by their indices
        # simple approach: move i forward until we pass earliest included file index
        # find max index included:
        max_idx = max(single_commits.index(f) for f in group_files)
        i = max_idx + 1
    return commits


def emit_all(commits: List[Dict], emitter: FastImportEmitter, tag_each_rev: bool):
    """
    Emit blobs and commits.
    Commits is list of dicts with keys: date_ts, author, log, files(list of SingleFileCommit), symbols(set)
    """
    # We'll assign blobs lazily when preparing the tree for a commit
    # maintain last_commit_mark_for_file in emitter
    for idx, commit in enumerate(commits):
        files = commit["files"]
        # prepare tree entries
        tree_entries: List[Tuple[str, Optional[int], str]] = []
        # determine parent candidate: use most recent last commit mark among involved files, if any
        candidate_parents = []
        for sf in files:
            bm = emitter._last_commit_mark_for_file.get(sf.filename)
            if bm:
                candidate_parents.append(bm)
        parent_mark = max(candidate_parents) if candidate_parents else None
        # for each file, ensure blob exists and collect mode
        for sf in files:
            # detect executable? crude: if content starts with shebang or original mode unknown - we'll default 644
            mode = "644"
            if sf.content.startswith("#!"):
                mode = "755"
            bmark = emitter.emit_blob(sf.filename, sf.rev_id, sf.content, mode=mode)
            tree_entries.append((sf.filename, bmark, mode))
        # compose log message, optionally prefix filename if single-file and requested
        logmsg = commit["log"] or ""
        if emitter.log_filename and len(files) == 1:
            logmsg = f"{files[0].filename}: {logmsg}"
        # create commit-level tags list (from symbols)
        tags = sorted(commit.get("symbols", []))
        # emit commit
        commit_mark = emitter.emit_commit(
            tree_entries,
            commit["author"],
            commit["date_ts"],
            logmsg,
            parent_mark,
            commit_tag_symbols=tags,
            commit_index=idx,
        )
        # optionally tag each rev individually
        if tag_each_rev:
            for sf in files:
                # tag name: filename@rev or rev
                t = sf.rev_id
                # create lightweight tag pointing to commit
                sys.stdout.write(f"reset refs/tags/{t}\n")
                sys.stdout.write(f"from :{commit_mark}\n")
                sys.stdout.write("\n")


# ---------- CLI ----------


def main():
    parser = argparse.ArgumentParser(
        description="Convert RCS *,v files into git fast-import stream using rlog/co."
    )
    parser.add_argument(
        "paths", nargs="+", help="RCS files or directories (will walk to find ,v files)"
    )
    parser.add_argument(
        "--authors-file", "-A", help="file with `username = Full Name <email>` mappings"
    )
    parser.add_argument(
        "--author-is-committer", action="store_true", help="use author as committer"
    )
    parser.add_argument(
        "--no-author-is-committer",
        dest="author_is_committer_no",
        action="store_true",
        help="do not use author as committer",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="ignore files matching this shell pattern (can be repeated)",
    )
    parser.add_argument(
        "--log-encoding", help="encoding of log messages in RCS files (e.g. ISO-8859-1)"
    )
    parser.add_argument(
        "--rcs-commit-fuzz",
        type=int,
        default=300,
        help="time fuzz (seconds) for coalescing commits (default 300)",
    )
    parser.add_argument(
        "--no-symbol-check",
        dest="symbol_check",
        action="store_false",
        help="do not check symbols when coalescing",
    )
    parser.add_argument(
        "--symbol-check",
        dest="symbol_check",
        action="store_true",
        help="check symbols when coalescing (default)",
    )
    parser.add_argument(
        "--tag-each-rev",
        action="store_true",
        help="create a lightweight tag for each RCS revision",
    )
    parser.add_argument(
        "--log-filename",
        action="store_true",
        help="prepend filename to commit logs for single-file imports",
    )
    parser.add_argument(
        "--skip-branches", action="store_true", help="skip branch-only revisions"
    )
    parser.add_argument(
        "--warn-missing-authors",
        action="store_true",
        help="warn about usernames not found in authors map",
    )
    parser.add_argument(
        "--expand-keywords",
        action="store_true",
        help="expand keywords (co handles keyword expansion)",
    )
    args = parser.parse_args()

    # authors map
    author_map = {}
    if args.authors_file:
        author_map = load_authors_file(args.authors_file, args.warn_missing_authors)
    # try to seed author map from environment/git config for current user
    try:
        import pwd, getpass

        current_user = getpass.getuser()
        if current_user not in author_map:
            # try to glean git config user.name and user.email
            try:
                name = subprocess.check_output(
                    ["git", "config", "user.name"], text=True
                ).strip()
                email = subprocess.check_output(
                    ["git", "config", "user.email"], text=True
                ).strip()
                if name and email:
                    author_map[current_user] = f"{name} <{email}>"
            except Exception:
                pass
    except Exception:
        pass

    histories = collect_histories(
        args.paths,
        args.ignore,
        args.log_encoding,
        args.expand_keywords,
        args.skip_branches,
    )
    if not histories:
        sys.stderr.write("No RCS histories found.\n")
        sys.exit(1)

    sys.stderr.write(f"Found {len(histories)} RCS files to import\n")
    for h in histories:
        sys.stderr.write(f"  {h}\n")

    # build single-file commits
    sfcs = build_single_file_commits(histories, args.expand_keywords)
    sys.stderr.write(f"Built {len(sfcs)} single-file revisions\n")

    # coalesce commits if more than one file
    if len(histories) == 1:
        # single-file export: do not coalesce across files (nothing to coalesce)
        # create commits in chronological order - each becomes a commit
        commits: List[Dict] = []
        for s in sfcs:
            commit = {
                "date_ts": s.date_ts,
                "author": s.author,
                "log": s.log,
                "files": [s],
                "symbols": set(s.symbols),
            }
            commits.append(commit)
    else:
        commits = coalesce_commits(
            sfcs,
            (
                args.rcs_commit_fuzz
                if hasattr(args, "rcs_commit_fuzz")
                else args.rcs_commit_fuzz
            ),
            args.symbol_check if hasattr(args, "symbol_check") else True,
            args.warn_missing_authors,
        )

    emitter = FastImportEmitter(
        author_map,
        author_is_committer=(
            args.author_is_committer and not args.author_is_committer_no
        ),
        tag_each_rev=args.tag_each_rev,
        log_filename=args.log_filename,
    )

    emit_all(commits, emitter, args.tag_each_rev)


if __name__ == "__main__":
    main()
