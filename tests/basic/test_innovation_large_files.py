from pathlib import Path

from aider.innovation_large_files import LargeFileDetector, SearchFirstEditor


def test_large_file_detector_and_search_windows():
    text = "\n".join(["filler"] * 50 + ["def target_symbol():", "    return 1"] + ["tail"] * 50)
    detector = LargeFileDetector(line_threshold=80, byte_threshold=10_000, token_threshold=10_000)
    profile = detector.profile("demo.py", text)
    assert profile.is_large
    windows = SearchFirstEditor().extract_windows(text, "target_symbol", context_lines=2)
    assert len(windows) == 1
    assert "target_symbol" in windows[0].text


def test_surgical_edit_refuses_stale_file(tmp_path: Path):
    path = tmp_path / "demo.py"
    original = "one\ntwo\nthree\n"
    path.write_text(original, encoding="utf-8")
    editor = SearchFirstEditor()
    edit = editor.make_edit(path, original, start_line=2, end_line=2, replacement="TWO")
    path.write_text("changed\n", encoding="utf-8")
    try:
        editor.apply(edit, allow_write=True)
    except RuntimeError as exc:
        assert "stale" in str(exc)
    else:
        raise AssertionError("expected stale edit refusal")


def test_surgical_edit_preview_and_apply(tmp_path: Path):
    path = tmp_path / "demo.py"
    original = "one\ntwo\nthree\n"
    path.write_text(original, encoding="utf-8")
    editor = SearchFirstEditor()
    edit = editor.make_edit(path, original, start_line=2, end_line=2, replacement="TWO")
    preview = editor.apply(edit)
    assert preview.changed
    assert "-two" in preview.diff
    assert path.read_text(encoding="utf-8") == original
    editor.apply(edit, allow_write=True)
    assert path.read_text(encoding="utf-8") == "one\nTWO\nthree\n"
