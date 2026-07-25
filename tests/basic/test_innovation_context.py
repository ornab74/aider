from aider.innovation_context import FloatingContextManager, FloatingContextState


def test_context_packet_prefers_query_and_explicit_file():
    files = {
        "small.py": "def unrelated():\n    return 1\n",
        "target.py": "\n".join(["# filler"] * 40 + ["def calculate_budget():", "    return 42"]),
    }
    state = FloatingContextState()
    state.pin("target.py")
    manager = FloatingContextManager(max_tokens=1000, reserve_tokens=200, state=state)
    packet = manager.build_packet(files, "fix calculate_budget", explicit_paths=["target.py"])
    assert packet.slices
    assert packet.slices[0].path == "target.py"
    assert packet.token_estimate <= packet.budget
    assert "calculate_budget" in packet.render()


def test_context_manager_rejects_impossible_budget():
    try:
        FloatingContextManager(max_tokens=100, reserve_tokens=100)
    except ValueError as exc:
        assert "larger" in str(exc)
    else:
        raise AssertionError("expected ValueError")
