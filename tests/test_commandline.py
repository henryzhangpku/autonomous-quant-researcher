from research.commandline import split_command


def test_windows_paths_keep_their_backslashes():
    raw = r"C:\Users\x\.venv\Scripts\python.exe C:\tmp\fake.py --describe"
    assert split_command(raw, windows=True) == (
        r"C:\Users\x\.venv\Scripts\python.exe", r"C:\tmp\fake.py", "--describe",
    )


def test_windows_quoted_path_with_spaces_is_one_unquoted_token():
    raw = r'"C:\Program Files\Python\python.exe" -m research.validators.options'
    assert split_command(raw, windows=True) == (
        r"C:\Program Files\Python\python.exe", "-m", "research.validators.options",
    )


def test_posix_split_is_unchanged():
    assert split_command("python -m mod --name 'a b'", windows=False) == (
        "python", "-m", "mod", "--name", "a b",
    )
