
import pytest
import subprocess
import os
import shutil

def test_rcs2git_integration():
    # Create a temporary directory for the git repository
    repo_dir = "test_repo"
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir)
    os.makedirs(repo_dir)

    try:
        # Initialize a git repository
        subprocess.run(["git", "init"], cwd=repo_dir, check=True)

        # Run rcs2git.py and pipe the output to git fast-import
        rcs2git_process = subprocess.Popen(
            ["python3", "rcs2git.py", "test_data/test_integration_file.txt,v"],
            stdout=subprocess.PIPE,
        )
        fast_import_process = subprocess.Popen(
            ["git", "fast-import"],
            cwd=repo_dir,
            stdin=rcs2git_process.stdout,
        )
        fast_import_process.communicate()
        rcs2git_process.wait()

        # Check the number of commits in the repository
        commit_count_output = subprocess.check_output(
            ["git", "rev-list", "--all", "--count"], cwd=repo_dir, text=True
        ).strip()
        assert commit_count_output == "2"

    finally:
        # Clean up the temporary directory
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir)
