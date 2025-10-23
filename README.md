# rcs2git

`rcs2git` is a Python script that converts RCS `*,v` files to a git fast-import stream. It uses the RCS command-line tools (`rlog` and `co`) to extract the history from the RCS files.

## Usage

```bash
python3 rcs2git.py [options] paths...
```

### Example

To convert an RCS project to a Git repository:

1.  Initialize a new Git repository:
    ```bash
    git init destrepo
    cd destrepo
    ```

2.  Run `rcs2git.py` and pipe the output to `git fast-import`:
    ```bash
    python3 /path/to/rcs2git.py /path/to/rcs_project --authors-file authors.txt | git fast-import
    ```

3.  Reset the repository to the latest commit:
    ```bash
    git reset
    ```

## Options

| Option                    | Description                                                                          |
| ------------------------- | ------------------------------------------------------------------------------------ |
| `paths`                   | One or more RCS files or directories. Directories will be walked to find `*,v` files. |
| `--authors-file`, `-A`    | File with `username = Full Name <email>` mappings.                                   |
| `--author-is-committer`   | Use the author as the committer.                                                     |
| `--no-author-is-committer`| Do not use the author as the committer.                                              |
| `--ignore`                | Ignore files matching this shell pattern (can be repeated).                          |
| `--log-encoding`          | Encoding of log messages in RCS files (e.g. `ISO-8859-1`).                             |
| `--rcs-commit-fuzz`       | Time fuzz (seconds) for coalescing commits (default: 300).                           |
| `--no-symbol-check`       | Do not check symbols when coalescing commits.                                        |
| `--symbol-check`          | Check symbols when coalescing commits (default).                                     |
| `--tag-each-rev`          | Create a lightweight tag for each RCS revision.                                      |
| `--log-filename`          | Prepend the filename to commit logs for single-file imports.                         |
| `--skip-branches`         | Skip branch-only revisions.                                                          |
| `--warn-missing-authors`  | Warn about usernames not found in the authors map.                                   |
| `--expand-keywords`       | Expand keywords (co handles keyword expansion).                                      |
