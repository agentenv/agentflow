from agentflow.specs import AgentKind
from agentflow.traces import create_trace_parser


def test_codex_trace_parser_extracts_assistant_message():
    parser = create_trace_parser(AgentKind.CODEX, "plan")
    events = parser.feed('{"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"codex ok"}]}}')
    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "codex ok"


def test_claude_trace_parser_extracts_result():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")
    parser.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')
    parser.feed('{"type":"result","result":"done"}')
    assert parser.finalize() == "working\ndone"


def test_claude_trace_parser_dedupes_matching_result():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")
    parser.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')
    parser.feed('{"type":"result","result":"working"}')
    assert parser.finalize() == "working"


def test_claude_trace_parser_ignores_hook_chatter():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")

    assert parser.feed('{"type":"system","subtype":"hook_started","hook_name":"SessionStart:startup"}') == []
    assert parser.feed('{"type":"system","subtype":"hook_response","hook_name":"SessionStart:startup","output":"very large startup payload"}') == []

    events = parser.feed('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')

    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "working"


def test_claude_trace_parser_keeps_hook_failures():
    parser = create_trace_parser(AgentKind.CLAUDE, "implement")

    events = parser.feed('{"type":"system","subtype":"hook_failed","hook_name":"SessionStart:startup","stderr":"hook exploded"}')

    assert events[0].kind == "hook_error"
    assert events[0].title == "Hook failed: SessionStart:startup"
    assert events[0].content == "hook exploded"


def test_kimi_trace_parser_extracts_text_part():
    parser = create_trace_parser(AgentKind.KIMI, "review")
    parser.feed('{"jsonrpc":"2.0","method":"event","params":{"type":"ContentPart","payload":{"type":"text","text":"kimi trace"}}}')
    assert parser.finalize() == "kimi trace"
