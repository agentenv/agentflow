from agentflow.specs import AgentKind
from agentflow.traces import create_trace_parser


def test_codex_trace_parser_extracts_assistant_message():
    parser = create_trace_parser(AgentKind.CODEX, "plan")
    events = parser.feed('{"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"codex ok"}]}}')
    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "codex ok"


def test_codex_trace_parser_ignores_unstable_feature_warning():
    parser = create_trace_parser(AgentKind.CODEX, "plan")

    assert parser.feed('{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Under-development features enabled: responses_websockets_v2. To suppress this warning, set suppress_unstable_features_warning = true in /home/shou/.codex/config.toml."}}') == []

    events = parser.feed('{"type":"response.output_item.done","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"codex ok"}]}}')

    assert events[0].kind == "assistant_message"
    assert parser.finalize() == "codex ok"


def test_codex_trace_parser_keeps_real_error_items():
    parser = create_trace_parser(AgentKind.CODEX, "plan")

    events = parser.feed('{"type":"item.completed","item":{"id":"item_0","type":"error","message":"permission denied"}}')

    assert events[0].kind == "item_completed"
    assert events[0].title == "Item completed: error"
    assert events[0].content == "permission denied"


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


def test_pi_trace_parser_extracts_final_assistant_text_from_agent_end():
    parser = create_trace_parser(AgentKind.PI, "scan")
    # Realistic Pi event sequence: session / agent_start / turn_start / message_start
    # (user) / message_end (user) / message_start (assistant) / message_update deltas /
    # message_end (assistant) / turn_end / agent_end.
    parser.feed('{"type":"session","id":"abc","cwd":"/tmp"}')
    parser.feed('{"type":"agent_start"}')
    parser.feed('{"type":"turn_start"}')
    parser.feed('{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"Hello"}}')
    parser.feed('{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":" there"}}')
    parser.feed(
        '{"type":"message_end","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"Hello there"}]}}'
    )
    parser.feed(
        '{"type":"agent_end","messages":['
        '{"role":"user","content":[{"type":"text","text":"say hi"}]},'
        '{"role":"assistant","content":[{"type":"text","text":"Hello there"}]}'
        "]}"
    )
    assert parser.finalize() == "Hello there"


def test_pi_trace_parser_emits_delta_events():
    parser = create_trace_parser(AgentKind.PI, "scan")
    events = parser.feed(
        '{"type":"message_update","assistantMessageEvent":{"type":"text_delta","delta":"partial"}}'
    )
    assert len(events) == 1
    assert events[0].kind == "assistant_delta"
    assert events[0].content == "partial"


def test_pi_trace_parser_prefers_agent_end_when_present():
    parser = create_trace_parser(AgentKind.PI, "scan")
    # Only feed agent_end with a single assistant message.
    parser.feed(
        '{"type":"agent_end","messages":['
        '{"role":"assistant","content":[{"type":"text","text":"final answer"}]}'
        "]}"
    )
    assert parser.finalize() == "final answer"
