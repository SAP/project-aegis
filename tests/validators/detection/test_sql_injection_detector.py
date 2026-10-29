"""Unit tests for ``SqlInjectionDetector`` — driven through a real ``Guard``."""

from __future__ import annotations

import pytest

from aegis import Guard
from aegis.validators.base import ContentItem, ContentType, ContentView
from aegis.validators.detection import SqlInjectionDetector


def _guard(**kwargs) -> Guard:
    return Guard("test").use(SqlInjectionDetector(**kwargs))


def _view(*values: str) -> ContentView:
    return ContentView(
        items=tuple(ContentItem(id=f"p{i}", type=ContentType.TEXT, value=v) for i, v in enumerate(values))
    )


async def test_clean_text_passes():
    result = await _guard().validate("Please update my shipping address to 42 Main Street.")
    assert not result.has_failures


async def test_failure_reports_position_and_construct():
    # A failure locates the first construct: offset (into the scanned text), length, and class.
    result = await _guard().validate("name=1 UNION SELECT pw FROM users")
    f = result.failures[0]
    assert f.details == {"match_offset": 7, "match_length": len("UNION SELECT"), "construct": "union_select"}
    assert "offset 7" in f.error_message


async def test_failure_position_points_at_earliest_construct():
    # Two constructs present; the earliest offset is reported with its construct class.
    result = await _guard().validate("admin OR 1=1 UNION SELECT x")
    f = result.failures[0]
    assert f.details["match_offset"] == 6
    assert f.details["construct"] == "tautology"


async def test_injection_constructs_fail():
    for payload in (
        "1 UNION SELECT password FROM users",
        "1 union all select 1,2,3",
        "'; DROP TABLE users; --",
        "admin' OR '1'='1",
        "x OR 1=1",
        "1 /*!50000UNION*/ SELECT pw FROM users",
        "name = 'x'--",
        "1; SELECT sleep(5)",
        "1); WAITFOR DELAY '0:0:5'--",
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED, payload


async def test_quoted_tautology_in_numeric_context_is_caught():
    # Injection point is numeric (no leading quote), tautology uses quoted operands.
    for payload in ("0 OR '1'='1'", "1 AND '1'='1'"):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED, payload


async def test_and_tautology_is_caught():
    # Boolean-blind AND tautologies, not only OR.
    for payload in ("1 AND 1=1", "x' AND 1=1-- "):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED, payload


async def test_insert_into_requires_sql_continuation():
    # Real INSERT (with VALUES / ( ) is caught; the English phrase "insert into the box" is not.
    assert (await _guard().validate("x; INSERT INTO users VALUES(1)")).has_failures
    assert (await _guard().validate("x; INSERT INTO users (a) VALUES (1)")).has_failures
    assert not (await _guard().validate("buy milk; insert into the box please")).has_failures


async def test_time_probe_requires_numeric_arg():
    # SLEEP(5)/pg_sleep(10)/BENCHMARK(n,...) are caught; "sleep (now)" prose is not.
    for payload in ("1; SELECT sleep(5)", "x; pg_sleep(10)", "y; BENCHMARK(1000000,MD5(1))"):
        assert (await _guard().validate(payload)).has_failures, payload
    for benign in ("go to sleep (now) please", "benchmark (results) look good"):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_vertical_tab_split_keyword_is_caught():
    # Regression: \x0b (vertical tab) and \x0c (form feed) are category Cc but match ``\s``, so an
    # all-word/whitespace payload would hit the fast path and skip stripping. It must still strip.
    for sep in ("\x0b", "\x0c"):
        payload = f"uni{sep}on select password from users"
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED, repr(payload)


async def test_tautology_requires_equal_operands():
    # Always-true comparisons are flagged; a false comparison and benign arithmetic are not.
    for taut in ("' OR '1'='1", "x OR 1=1", "OR 10=10"):
        assert (await _guard().validate(taut)).has_failures, taut
    for not_taut in ("' OR 'admin'='guest'", "x OR 1=2", "pick 2 or 3 = 5 apples", "OR 1=10"):
        assert not (await _guard().validate(not_taut)).has_failures, not_taut


async def test_url_encoded_payload_is_caught():
    # %20 → space; the UNION SELECT marker only matches after decoding.
    result = await _guard().validate("1%20UNION%20SELECT%20password%20FROM%20users")
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED


async def test_zero_width_split_keyword_is_caught():
    # A zero-width space inside the keyword must not hide "union select".
    result = await _guard().validate("1 uni​on select password from users")
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED


async def test_encoded_zero_width_split_keyword_is_caught():
    # Regression: the zero-width space is URL-encoded (%E2%80%8B), so it only becomes an invisible
    # char *after* decoding. Stripping must run again after each decode pass, or the revealed
    # separator keeps "union select" from matching.
    result = await _guard().validate("uni%E2%80%8Bon select password from users")
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED


async def test_mysql_exec_comment_is_caught():
    # /*!... */ runs in MySQL only (a SQLi evasion wrapper); the opener is flagged, including one
    # left unterminated to swallow the rest of a query. A plain /* comment is NOT flagged.
    for payload in ("1 /*!50000UNION*/ SELECT pw", "SELECTED name /*! and the rest commented out"):
        result = await _guard().validate(payload)
        assert result.failures[0].details["construct"] == "mysql_exec_comment", payload
    assert not (await _guard().validate("value /* inline comment */ more")).has_failures


async def test_private_use_char_is_not_stripped_so_no_false_merge():
    # Regression: only Cc/Cf are stripped, not Co/Cs/Cn. A private-use char (U+E000, category
    # Co) between "uni" and "on" must survive, so it does NOT merge into "union select".
    payload = "uni" + chr(0xE000) + "on select something"
    assert not (await _guard().validate(payload)).has_failures


async def test_benign_prose_with_or_does_not_false_positive():
    for benign in (
        "Choose red or blue, whichever you prefer.",
        "Select the option that works best for you.",
        "I will union the two teams next quarter.",
    ):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_decode_toggle_off_lets_encoded_payload_pass():
    result = await _guard(decode_obfuscation=False, strip_invisible_chars=False).validate("1%20UNION%20SELECT%20x")
    assert not result.has_failures


async def test_strip_invisible_toggle_off_lets_split_keyword_pass():
    result = await _guard(strip_invisible_chars=False, decode_obfuscation=False).validate("1 uni​on select x")
    assert not result.has_failures


async def test_empty_and_whitespace_pass():
    assert not (await _guard().validate("")).has_failures
    assert not (await _guard().validate("    \t\n")).has_failures


async def test_multi_item_view_with_a_construct_fails():
    view = _view("hello there", "1 UNION SELECT a FROM b", "goodbye")
    result = await _guard().validate(view)
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED


async def test_construct_split_across_items_is_caught():
    # Detection runs on view.render() (items joined by newline, matched by \\s), so a construct
    # split across an item boundary is still caught — not evaluated per item in isolation.
    view = _view("1 UNION", "SELECT password FROM users")
    result = await _guard().validate(view)
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED


async def test_prose_with_verb_after_semicolon_does_not_false_positive():
    # The stacked-query pattern pairs each verb with its SQL object, so ordinary prose that puts a
    # common verb after a semicolon is not flagged.
    for benign in (
        "Log in; create your profile; update settings later.",
        "see item; #3 is the best option",
        "I will drop by; delete the note when done",
    ):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_stacked_query_with_sql_object_fails():
    for payload in (
        "a; delete from users",
        "x; drop table t",
        "y; exec xp_cmdshell",
        "x; drop procedure sp_foo",
        "x; drop function f",
        "x; drop trigger trg",
    ):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED, payload


async def test_stacked_exec_of_qualified_proc_fails():
    # A qualified (dotted) stored-proc call after ; EXEC is caught, as is a call form.
    for payload in ("x; EXEC dbo.dangerous_proc", "x; EXEC master.dbo.xp_cmdshell", "x; exec (sql)"):
        result = await _guard().validate(payload)
        assert result.failures[0].error_code == SqlInjectionDetector.SQLI_DETECTED, payload


async def test_execute_verb_in_prose_does_not_false_positive():
    # 'execute' as an English verb (bare word, no qualification/call) is not flagged.
    for benign in ("please execute the plan next", "list; execute the task", "note; executed the job"):
        assert not (await _guard().validate(benign)).has_failures, benign


async def test_tautology_prefix_is_not_a_false_match():
    # ' OR '1'='10 is NOT a tautology (1 != 10); the backreference must not match only the prefix.
    assert not (await _guard().validate("' OR '1'='10")).has_failures
    assert (await _guard().validate("' OR '12'='12")).has_failures


async def test_max_input_chars_rejects_oversized_input():
    result = await _guard(max_input_chars=10).validate("x" * 20)
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_INPUT_TOO_LARGE
    assert result.failures[0].details == {"length": 20, "limit": 10}


async def test_default_max_input_chars_caps_at_one_mib():
    # Safe-by-default: oversized input is rejected without being preprocessed/scanned.
    result = await _guard().validate("a" * (1024 * 1024 + 1))
    assert result.failures[0].error_code == SqlInjectionDetector.SQLI_INPUT_TOO_LARGE


async def test_max_input_chars_none_lifts_the_cap():
    # Explicit opt-out for trusted-size input.
    assert not (await _guard(max_input_chars=None).validate("a" * (1024 * 1024 + 1))).has_failures


def test_negative_max_input_chars_rejected_at_construction():
    with pytest.raises(ValueError):
        SqlInjectionDetector(max_input_chars=-1)
