import pytest
from rcs2git import parse_rcs_date, UTC
import datetime
import time
import subprocess
from unittest.mock import patch


def test_parse_rcs_date():
    # Standard RCS date format
    dt = datetime.datetime(2023, 10, 27, 10, 30, 0, tzinfo=UTC())
    assert parse_rcs_date("2023.10.27.10.30.00") == int(dt.timestamp())

    # 2-digit year (should be treated as 19xx)
    dt = datetime.datetime(1999, 12, 31, 23, 59, 59, tzinfo=UTC())
    assert parse_rcs_date("99.12.31.23.59.59") == int(dt.timestamp())

    # ISO format fallback
    dt = datetime.datetime(2023, 10, 27, 10, 30, 0, tzinfo=UTC())
    assert parse_rcs_date("2023-10-27T10:30:00Z") == int(dt.timestamp())

    # Invalid format should return current time (approximately)
    now = int(time.time())
    assert parse_rcs_date("invalid date") == pytest.approx(now, abs=2)


def test_rcs2git_integration():
    # Mock rlog command
    rlog_output = """
revision 1.2
date: 2023.11.21.12.00.00; author: testuser; state: Exp;
log
Second revision
text
=============================================================================
revision 1.1
date: 2023.11.21.11.00.00; author: testuser; state: Exp;
log
Initial revision
text
=============================================================================
"""

    # Mock co command for revision 1.1
    co_output_1_1 = "Hello, world!"

    # Mock co command for revision 1.2
    co_output_1_2 = "Hello, world!\\nThis is the second revision."

    def mock_subprocess(cmd, universal_newlines=True, stderr=None):
        if cmd[0] == "rlog":
            return rlog_output
        elif cmd[0] == "co":
            if cmd[1] == "-p1.1":
                return co_output_1_1
            elif cmd[1] == "-p1.2":
                return co_output_1_2
        return ""

    with patch(
        "subprocess.check_output", side_effect=mock_subprocess
    ) as mock_check_output:
        # Run the script
        from rcs2git import main
        from io import StringIO
        import sys

        saved_stdout = sys.stdout
        try:
            out = StringIO()
            sys.stdout = out
            with patch("sys.argv", ["rcs2git.py", "test_data/test_file.txt,v"]):
                main()
            output = out.getvalue()
        finally:
            sys.stdout = saved_stdout

        # Assertions to check the output
        assert "blob" in output
        assert "commit refs/heads/master" in output
        assert "author testuser" in output
        assert "Initial revision" in output
        assert "Second revision" in output
