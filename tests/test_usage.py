"""Tests for live quota ConnectRPC integration and SQLite analytics in relay/usage.py."""

import datetime
import json
import re
import sqlite3
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

import context
context.isolate_state()

from relay import usage

report = context.Report()
check = report.check

# HTML validator based on tests/test_replies.py
TAGS = ("b", "strong", "i", "em", "u", "s", "code", "pre")
ENTITIES = ("&amp;", "&lt;", "&gt;", "&quot;")


def is_valid_telegram_html(text: str) -> bool:
    stripped = text
    for tag in TAGS:
        stripped = stripped.replace(f"<{tag}>", "").replace(f"</{tag}>", "")
    if "<" in stripped or ">" in stripped:
        return False
    for entity in ENTITIES:
        stripped = stripped.replace(entity, "")
    if "&" in stripped:
        return False
    return True


# Helper to synthesize Protobuf UsageMetadata blobs
def encode_varint(n: int) -> bytes:
    res = bytearray()
    while n > 0x7F:
        res.append((n & 0x7F) | 0x80)
        n >>= 7
    res.append(n & 0x7F)
    return bytes(res)


def encode_field(fn: int, wt: int, data: bytes) -> bytes:
    key = (fn << 3) | wt
    return encode_varint(key) + data


def make_synthetic_usage_blob(uncached_prompt: int, cached_prompt: int, candidate: int) -> bytes:
    u = bytearray()
    if uncached_prompt > 0:
        u += encode_field(2, 0, encode_varint(uncached_prompt))
    if cached_prompt > 0:
        u += encode_field(5, 0, encode_varint(cached_prompt))
    if candidate > 0:
        u += encode_field(3, 0, encode_varint(candidate))
    f4 = encode_field(4, 2, encode_varint(len(u)) + bytes(u))
    f1 = encode_field(1, 2, encode_varint(len(f4)) + f4)
    return f1


print("\n--- 1. Pure-Python Protobuf Varint Decoder ---")
# 1.1 Synthetic blob with prompt and candidate tokens
blob1 = make_synthetic_usage_blob(100, 50, 25)
p1, c1 = usage.decode_usage_protobuf(blob1)
check("synthetic blob decoded prompt tokens (uncached + cached)", p1 == 150, f"got {p1}")
check("synthetic blob decoded candidate tokens", c1 == 25, f"got {c1}")

# 1.2 Empty blob
p_empty, c_empty = usage.decode_usage_protobuf(b"")
check("empty bytes returns 0, 0", (p_empty, c_empty) == (0, 0), str((p_empty, c_empty)))

# 1.3 None or invalid types
p_none, c_none = usage.decode_usage_protobuf(None)
check("None returns 0, 0", (p_none, c_none) == (0, 0), str((p_none, c_none)))

# 1.4 Corrupted / truncated bytes
p_corrupt, c_corrupt = usage.decode_usage_protobuf(b"\xff\xff\xff\x7f\x12\x34")
check("corrupted bytes handled gracefully without raising", (p_corrupt, c_corrupt) == (0, 0))

p_trunc, c_trunc = usage.decode_usage_protobuf(b"\x0a\x20\x12")
check("truncated bytes handled gracefully without raising", (p_trunc, c_trunc) == (0, 0))


print("\n--- 2. ConnectRPC Response Parsing & Quota Calculations ---")
client = usage.LanguageServerClient()

# 2.1 Valid ConnectRPC response
sample_resp = {
    "userStatus": {
        "userTier": {"name": "Google AI Pro"},
        "cascadeModelConfigData": {
            "clientModelConfigs": [
                {
                    "label": "Gemini 3.8 Flash (High)",
                    "quotaInfo": {
                        "remainingFraction": 0.400022,
                        "resetTime": "2026-10-09T19:27:19Z"
                    }
                }
            ]
        }
    }
}
parsed_quota = client.parse_quota(sample_resp)
check("parse_quota extracts remainingFraction and resetTime",
      parsed_quota is not None and abs(parsed_quota[0] - 0.400022) < 1e-6 and parsed_quota[1] == "2026-10-09T19:27:19Z",
      str(parsed_quota))

# 2.2 Missing quotaInfo or null remainingFraction
bad_resp_1 = {"userStatus": {"cascadeModelConfigData": {"clientModelConfigs": [{"label": "Empty"}]}}}
check("parse_quota handles missing quotaInfo", client.parse_quota(bad_resp_1) is None)

bad_resp_2 = {}
check("parse_quota handles empty dict", client.parse_quota(bad_resp_2) is None)

check("parse_quota handles None", client.parse_quota(None) is None)

# 2.3 Progress bar rendering
check("progress bar 0%", usage.make_progress_bar(0.0) == "░░░░░░░░░░")
check("progress bar 40%", usage.make_progress_bar(0.4) == "████░░░░░░")
check("progress bar 100%", usage.make_progress_bar(1.0) == "██████████")
check("progress bar clamped negative", usage.make_progress_bar(-0.5) == "░░░░░░░░░░")
check("progress bar clamped > 1", usage.make_progress_bar(1.5) == "██████████")

# 2.4 Countdown calculation
ref_now = datetime.datetime(2026, 10, 9, 17, 0, 0, tzinfo=datetime.timezone.utc)
check("countdown 2h 21m",
      usage.format_countdown("2026-10-09T19:21:00Z", now=ref_now) == "2h 21m")
check("countdown 45m",
      usage.format_countdown("2026-10-09T17:45:00Z", now=ref_now) == "45m")
check("countdown past resets to 0m",
      usage.format_countdown("2026-10-09T16:00:00Z", now=ref_now) == "0m")
check("countdown invalid timestamp returns unknown",
      usage.format_countdown("not-a-date", now=ref_now) == "unknown")

# 2.5 Token and Step formatting
check("format_tokens under 1k", usage.format_tokens(500) == "500 tokens")
check("format_tokens 1 token", usage.format_tokens(1) == "1 token")
check("format_tokens thousands", usage.format_tokens(12400) == "12.4k tokens")
check("format_tokens millions", usage.format_tokens(10628165) == "10.6M tokens")
check("format_steps 1 step", usage.format_steps(1) == "1 step")
check("format_steps thousands", usage.format_steps(2222) == "2,222 steps")


print("\n--- 3. CSRF & Port Discovery Utilities ---")
# 3.1 CSRF extraction regex logic
test_cmdlines = [
    r"C:\bin\language_server.exe --standalone --csrf_token c8e906f0-cf9e-4297-9d49-6f7c6a160001 --port 0",
    r"language_server.exe --csrf_token=12345678-abcd-1234-abcd-1234567890ab --other flag",
    r"--csrf_token  deadbeef-0000-1111-2222-333344445555",
]
m1 = re.search(r"--csrf_token(?:=|\s+)([a-f0-9-]+)", test_cmdlines[0], re.IGNORECASE)
m2 = re.search(r"--csrf_token(?:=|\s+)([a-f0-9-]+)", test_cmdlines[1], re.IGNORECASE)
m3 = re.search(r"--csrf_token(?:=|\s+)([a-f0-9-]+)", test_cmdlines[2], re.IGNORECASE)
check("csrf extracted from space separated flag", m1 and m1.group(1) == "c8e906f0-cf9e-4297-9d49-6f7c6a160001")
check("csrf extracted from equal separated flag", m2 and m2.group(1) == "12345678-abcd-1234-abcd-1234567890ab")
check("csrf extracted from multi-space flag", m3 and m3.group(1) == "deadbeef-0000-1111-2222-333344445555")

# 3.2 Port discovery from log file
tmp_log_dir = Path(tempfile.mkdtemp(prefix="relay-test-usage-log-"))
tmp_log_file = tmp_log_dir / "language_server.log"
tmp_log_file.write_text(
    "Language server listening on random port at 52563 for HTTPS (gRPC)\n"
    "Language server listening on random port at 52564 for HTTP\n"
    "Other log line\n",
    encoding="utf-8"
)
test_client = usage.LanguageServerClient(log_path=tmp_log_file)
port = test_client.get_http_port()
check("http port extracted from log file", port == 52564, f"got {port}")

tmp_log_file.write_text("No port information in this log\n", encoding="utf-8")
check("returns None when log has no http port line", test_client.get_http_port() is None)

missing_client = usage.LanguageServerClient(log_path=tmp_log_dir / "non_existent.log")
check("returns None when log file does not exist", missing_client.get_http_port() is None)


print("\n--- 4. ConnectRPC Network Client (Mocked) ---")
class MockUrlOpenResponse:
    def __init__(self, data: bytes, code: int = 200):
        self._data = data
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._data


captured_requests = []
original_urlopen = urllib.request.urlopen


def mock_urlopen_success(req, timeout=None):
    captured_requests.append({
        "url": req.full_url,
        "method": req.get_method(),
        "headers": dict(req.headers),
        "data": req.data,
        "timeout": timeout,
    })
    resp_body = json.dumps(sample_resp).encode("utf-8")
    return MockUrlOpenResponse(resp_body, 200)


try:
    urllib.request.urlopen = mock_urlopen_success
    res = test_client.query_user_status(52564, "my-csrf-token", timeout=1.0)
    check("query_user_status returns parsed JSON", res is not None and "userStatus" in res)
    check("connect-rpc endpoint called with POST", captured_requests[0]["method"] == "POST")
    check("connect-rpc version header present",
          captured_requests[0]["headers"].get("Connect-protocol-version") == "1")
    check("csrf token header present",
          captured_requests[0]["headers"].get("X-codeium-csrf-token") == "my-csrf-token")
    check("body is empty JSON object", captured_requests[0]["data"] == b"{}")
    check("timeout matches requested budget", captured_requests[0]["timeout"] == 1.0)
finally:
    urllib.request.urlopen = original_urlopen


def mock_urlopen_error(req, timeout=None):
    raise urllib.error.URLError("Connection refused")


try:
    urllib.request.urlopen = mock_urlopen_error
    err_res = test_client.query_user_status(52564, "my-csrf-token", timeout=1.0)
    check("query_user_status handles URLError gracefully", err_res is None)
finally:
    urllib.request.urlopen = original_urlopen


print("\n--- 5. SQLite Metrics Aggregator & In-Memory Caching ---")
tmp_db_dir = Path(tempfile.mkdtemp(prefix="relay-test-usage-dbs-"))
conv_dir = tmp_db_dir / "conversations"
conv_dir.mkdir(parents=True, exist_ok=True)

# Create conversation_summaries.db
summaries_path = tmp_db_dir / "conversation_summaries.db"
conn_sum = sqlite3.connect(summaries_path)
conn_sum.execute("""
    CREATE TABLE conversation_summaries (
        conversation_id TEXT PRIMARY KEY,
        step_count INTEGER,
        last_modified_time TEXT
    )
""")

now_dt = datetime.datetime.now(datetime.timezone.utc)
time_1h_ago = (now_dt - datetime.timedelta(hours=1)).isoformat()
time_10h_ago = (now_dt - datetime.timedelta(hours=10)).isoformat()
time_10d_ago = (now_dt - datetime.timedelta(days=10)).isoformat()

conn_sum.executemany("""
    INSERT INTO conversation_summaries (conversation_id, step_count, last_modified_time)
    VALUES (?, ?, ?)
""", [
    ("conv_1", 10, time_1h_ago),   # Inside 4h & 7d
    ("conv_2", 20, time_10h_ago),  # Outside 4h, Inside 7d
    ("conv_3", 50, time_10d_ago),  # Outside 7d
])
conn_sum.commit()
conn_sum.close()

# Create conversation trajectory databases
def create_conv_db(cid: str, uncached: int, cached: int, cand: int):
    p = conv_dir / f"{cid}.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE gen_metadata (idx INTEGER, size INTEGER, data BLOB)")
    blob = make_synthetic_usage_blob(uncached, cached, cand)
    c.execute("INSERT INTO gen_metadata (idx, size, data) VALUES (0, ?, ?)", (len(blob), blob))
    c.commit()
    c.close()

# conv_1: 1,000 prompt + 200 candidate = 1,200 tokens
create_conv_db("conv_1", 800, 200, 200)
# conv_2: 3,000 prompt + 500 candidate = 3,500 tokens
create_conv_db("conv_2", 2500, 500, 500)
# conv_3: 10,000 prompt + 1,000 candidate = 11,000 tokens
create_conv_db("conv_3", 8000, 2000, 1000)

scanner = usage.LocalMetricsScanner(root_dir=tmp_db_dir)
usage._TOKEN_CACHE.clear()

metrics_first = scanner.get_metrics()
check("steps_4h matches conv_1 steps", metrics_first["steps_4h"] == 10, f"got {metrics_first['steps_4h']}")
check("tokens_4h matches conv_1 tokens (1,200)", metrics_first["tokens_4h"] == 1200, f"got {metrics_first['tokens_4h']}")
check("steps_7d matches conv_1 + conv_2 steps (30)", metrics_first["steps_7d"] == 30, f"got {metrics_first['steps_7d']}")
check("tokens_7d matches conv_1 + conv_2 tokens (4,700)", metrics_first["tokens_7d"] == 4700, f"got {metrics_first['tokens_7d']}")

# In-memory cache test
check("cache was populated for conv_1", ("conv_1", 10) in usage._TOKEN_CACHE)
check("cache was populated for conv_2", ("conv_2", 20) in usage._TOKEN_CACHE)

# Delete conv_1.db file to prove second scan reads from memory cache
(conv_dir / "conv_1.db").unlink()
metrics_cached = scanner.get_metrics()
check("cached run yields identical tokens without file", metrics_cached["tokens_4h"] == 1200)

# Edge case: non-existent root_dir
empty_scanner = usage.LocalMetricsScanner(root_dir=tmp_db_dir / "non_existent_folder")
empty_metrics = empty_scanner.get_metrics()
check("missing DB returns zero metrics cleanly",
      empty_metrics == {"steps_4h": 0, "tokens_4h": 0, "steps_7d": 0, "tokens_7d": 0})


print("\n--- 6. Resilient Offline Fallback & Error Resilience ---")
class MockOfflineClient:
    def get_status(self):
        return {"online": False}


class MockBrokenClient:
    def get_status(self):
        raise RuntimeError("Unexpected socket exception")


offline_data = usage.get_usage_data(client=MockOfflineClient(), scanner=scanner)
check("offline data has online == False", offline_data["online"] is False)
check("offline data retains local steps_4h", offline_data["steps_4h"] == 10)
check("offline data retains local tokens_4h", offline_data["tokens_4h"] == 1200)

offline_card = usage.format_usage_card(data=offline_data)
check("offline card contains header", "📊 <b>AI Usage &amp; Quota</b>" in offline_card)
check("offline card shows Offline status indicator", "• Status: ⚪ Offline (AntiGravity closed)" in offline_card)
check("offline card displays 4h steps and tokens", "• Last 4 Hours: 10 steps · 1.2k tokens" in offline_card)
check("offline card displays 7d steps and tokens", "• Last 7 Days: 30 steps · 4.7k tokens" in offline_card)
check("offline card is valid Telegram HTML", is_valid_telegram_html(offline_card))


print("\n--- 7. Format Usage Card Outputs ---")
online_data = {
    "online": True,
    "remaining_fraction": 0.400022,
    "reset_time": "2026-10-09T19:27:19Z",
    "countdown": "2h 21m",
    "steps_4h": 2222,
    "tokens_4h": 10628165,
    "steps_7d": 6426,
    "tokens_7d": 38400000,
}
online_card = usage.format_usage_card(data=online_data)
check("online card contains header", "📊 <b>AI Usage &amp; Quota</b>" in online_card)
check("online card contains progress bar", "[████░░░░░░] 40.0% · Resets in 2h 21m" in online_card)
check("online card contains 4h activity", "• Last 4 Hours: 2,222 steps · 10.6M tokens" in online_card)
check("online card contains 7d activity", "• Last 7 Days: 6,426 steps · 38.4M tokens" in online_card)
check("online card is valid Telegram HTML", is_valid_telegram_html(online_card))


print("\n--- 8. Execution Time Benchmark (< 200ms) ---")
# Warm benchmark
t_start = time.perf_counter()
bench_card = usage.format_usage_card(client=MockOfflineClient(), scanner=scanner)
elapsed_ms = (time.perf_counter() - t_start) * 1000
check("execution time is well below 200ms", elapsed_ms < 200, f"{elapsed_ms:.2f} ms")

sys.exit(report.finish())
