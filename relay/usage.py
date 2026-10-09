"""Live quota and token activity monitoring for AntiGravity.

Queries the ConnectRPC status endpoint of language_server.exe for live 4-hour
quota fraction and countdown timer, and scans local SQLite conversation
databases in read-only mode for 4-hour and 7-day activity metrics.
"""

import ctypes
import datetime
import html
import json
import os
import re
import socket
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

CONNECT_RPC_PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
CONNECT_PROTOCOL_VERSION = "1"
HTTP_TIMEOUT = 1.0

# In-memory token cache keyed by (conv_id, step_count) -> total_tokens
_TOKEN_CACHE: Dict[Tuple[str, int], int] = {}


def parse_iso_datetime(dt_str: str) -> Optional[datetime.datetime]:
    """Parse ISO-8601 UTC timestamp safely."""
    if not dt_str:
        return None
    try:
        cleaned = dt_str.strip().replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception:
        return None


def format_countdown(reset_time_str: str, now: Optional[datetime.datetime] = None) -> str:
    """Compute human-friendly countdown (e.g. '2h 21m' or '45m') until reset_time."""
    dt = parse_iso_datetime(reset_time_str)
    if not dt:
        return "unknown"
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    diff = dt - now
    total_seconds = int(diff.total_seconds())
    if total_seconds <= 0:
        return "0m"
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def make_progress_bar(fraction: float, blocks: int = 10) -> str:
    """Generate a unicode block progress bar [████░░░░░░]."""
    clamped = max(0.0, min(1.0, float(fraction)))
    filled = int(round(clamped * blocks))
    empty = blocks - filled
    return "█" * filled + "░" * empty


def format_tokens(count: int) -> str:
    """Format token count into readable summary (e.g. '10.6M tokens', '12.4k tokens')."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M tokens"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k tokens"
    if count == 1:
        return "1 token"
    return f"{count:,} tokens"


def format_steps(count: int) -> str:
    """Format step count."""
    if count == 1:
        return "1 step"
    return f"{count:,} steps"


def decode_usage_protobuf(data: bytes) -> Tuple[int, int]:
    """Pure-Python varint reader decoding UsageMetadata from serialized protobuf blob.

    Returns (prompt_tokens, candidate_tokens).
    """
    if not data or not isinstance(data, (bytes, bytearray)):
        return 0, 0

    l = len(data)
    prompt = 0
    candidate = 0

    def read_varint(pos: int) -> Tuple[Optional[int], Optional[int]]:
        val = 0
        shift = 0
        while pos < l:
            b = data[pos]
            pos += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                return val, pos
            shift += 7
            if shift >= 64:
                return None, None
        return None, None

    i = 0
    try:
        while i < l:
            key, i = read_varint(i)
            if key is None:
                break
            fn = key >> 3
            wt = key & 7
            if wt == 0:
                _, i = read_varint(i)
                if i is None:
                    break
            elif wt == 2:
                sub_len, i = read_varint(i)
                if sub_len is None:
                    break
                sub_end = i + sub_len
                if sub_end > l:
                    break
                # UsageMetadata may be in field 1 -> field 4, or directly in field 4
                if fn == 1:
                    j = i
                    while j < sub_end:
                        k2, j = read_varint(j)
                        if k2 is None:
                            break
                        fn2 = k2 >> 3
                        wt2 = k2 & 7
                        if wt2 == 0:
                            _, j = read_varint(j)
                            if j is None:
                                break
                        elif wt2 == 2:
                            s2_len, j = read_varint(j)
                            if s2_len is None:
                                break
                            s2_end = j + s2_len
                            if s2_end > sub_end:
                                break
                            if fn2 == 4:
                                k = j
                                while k < s2_end:
                                    k3, k = read_varint(k)
                                    if k3 is None:
                                        break
                                    fn3 = k3 >> 3
                                    wt3 = k3 & 7
                                    if wt3 == 0:
                                        v3, k = read_varint(k)
                                        if v3 is None:
                                            break
                                        if fn3 in (2, 5):
                                            prompt += v3
                                        elif fn3 == 3:
                                            candidate += v3
                                    elif wt3 == 2:
                                        vl, k = read_varint(k)
                                        if vl is None:
                                            break
                                        k += vl
                                    elif wt3 == 1:
                                        k += 8
                                    elif wt3 == 5:
                                        k += 4
                                    else:
                                        break
                            j = s2_end
                        elif wt2 == 1:
                            j += 8
                        elif wt2 == 5:
                            j += 4
                        else:
                            break
                elif fn == 4:
                    k = i
                    while k < sub_end:
                        k3, k = read_varint(k)
                        if k3 is None:
                            break
                        fn3 = k3 >> 3
                        wt3 = k3 & 7
                        if wt3 == 0:
                            v3, k = read_varint(k)
                            if v3 is None:
                                break
                            if fn3 in (2, 5):
                                prompt += v3
                            elif fn3 == 3:
                                candidate += v3
                        elif wt3 == 2:
                            vl, k = read_varint(k)
                            if vl is None:
                                break
                            k += vl
                        elif wt3 == 1:
                            k += 8
                        elif wt3 == 5:
                            k += 4
                        else:
                            break
                i = sub_end
            elif wt == 1:
                i += 8
            elif wt == 5:
                i += 4
            else:
                break
    except Exception:
        pass
    return prompt, candidate


class LanguageServerClient:
    """Discovers and queries the local language_server.exe ConnectRPC endpoint."""

    def __init__(self, log_path: Optional[Union[str, Path]] = None, host: str = "127.0.0.1"):
        self.host = host
        if log_path:
            self.log_path = Path(log_path)
        else:
            appdata = os.environ.get("APPDATA", "")
            self.log_path = Path(appdata) / "Antigravity" / "logs" / "language_server.log" if appdata else None

    def find_process(self) -> Optional[int]:
        """Discover PID of running language_server.exe via CreateToolhelp32Snapshot (< 5ms)."""
        if sys.platform != "win32":
            return None
        try:
            kernel32 = ctypes.windll.kernel32
            TH32CS_SNAPPROCESS = 0x00000002

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", ctypes.c_ulong),
                    ("cntUsage", ctypes.c_ulong),
                    ("th32ProcessID", ctypes.c_ulong),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", ctypes.c_ulong),
                    ("cntThreads", ctypes.c_ulong),
                    ("th32ParentProcessID", ctypes.c_ulong),
                    ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong),
                    ("szExeFile", ctypes.c_wchar * 260)
                ]

            hSnap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if not hSnap or hSnap == -1:
                return None
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if kernel32.Process32FirstW(hSnap, ctypes.byref(entry)):
                    while True:
                        if entry.szExeFile.lower() == "language_server.exe":
                            return entry.th32ProcessID
                        if not kernel32.Process32NextW(hSnap, ctypes.byref(entry)):
                            break
            finally:
                kernel32.CloseHandle(hSnap)
        except Exception:
            pass
        return None

    def get_csrf_token(self, pid: int) -> Optional[str]:
        """Extract --csrf_token via PEB command line in < 5ms."""
        if sys.platform != "win32" or not pid:
            return None
        try:
            kernel32 = ctypes.windll.kernel32
            ntdll = ctypes.windll.ntdll

            PROCESS_QUERY_INFORMATION = 0x0400
            PROCESS_VM_READ = 0x0010

            class PROCESS_BASIC_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("ExitStatus", ctypes.c_ulonglong),
                    ("PebBaseAddress", ctypes.c_ulonglong),
                    ("AffinityMask", ctypes.c_ulonglong),
                    ("BasePriority", ctypes.c_ulonglong),
                    ("UniqueProcessId", ctypes.c_ulonglong),
                    ("InheritedFromUniqueProcessId", ctypes.c_ulonglong)
                ]

            hProc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not hProc:
                return None
            try:
                pbi = PROCESS_BASIC_INFORMATION()
                ret_len = ctypes.c_ulong()
                status = ntdll.NtQueryInformationProcess(
                    hProc, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
                )
                if status != 0 or not pbi.PebBaseAddress:
                    return None

                is_64bit = ctypes.sizeof(ctypes.c_void_p) == 8
                params_offset = 0x20 if is_64bit else 0x10
                cmdline_offset = 0x70 if is_64bit else 0x40
                buffer_ptr_offset = 0x78 if is_64bit else 0x44

                params_ptr = ctypes.c_ulonglong() if is_64bit else ctypes.c_ulong()
                if not kernel32.ReadProcessMemory(
                    hProc, ctypes.c_void_p(pbi.PebBaseAddress + params_offset),
                    ctypes.byref(params_ptr), ctypes.sizeof(params_ptr), None
                ) or not params_ptr.value:
                    return None

                cmd_len = ctypes.c_ushort()
                if not kernel32.ReadProcessMemory(
                    hProc, ctypes.c_void_p(params_ptr.value + cmdline_offset),
                    ctypes.byref(cmd_len), ctypes.sizeof(cmd_len), None
                ) or not cmd_len.value:
                    return None

                cmd_buf_ptr = ctypes.c_ulonglong() if is_64bit else ctypes.c_ulong()
                if not kernel32.ReadProcessMemory(
                    hProc, ctypes.c_void_p(params_ptr.value + buffer_ptr_offset),
                    ctypes.byref(cmd_buf_ptr), ctypes.sizeof(cmd_buf_ptr), None
                ) or not cmd_buf_ptr.value:
                    return None

                buf = ctypes.create_unicode_buffer(cmd_len.value // 2)
                if not kernel32.ReadProcessMemory(
                    hProc, ctypes.c_void_p(cmd_buf_ptr.value), buf, cmd_len.value, None
                ):
                    return None

                cmdline = buf.value
                m = re.search(r"--csrf_token(?:=|\s+)([a-f0-9-]+)", cmdline, re.IGNORECASE)
                if m:
                    return m.group(1)
            finally:
                kernel32.CloseHandle(hProc)
        except Exception:
            pass
        return None

    def get_http_port(self, pid: Optional[int] = None) -> Optional[int]:
        """Discover HTTP port from language_server.log or iphlpapi fallback."""
        if self.log_path and self.log_path.exists():
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="ignore") as f:
                    head = f.read(8192)
                m = re.search(r"listening on \w+ port at (\d+) for HTTP\b", head)
                if m:
                    return int(m.group(1))
            except Exception:
                pass

        if pid and sys.platform == "win32":
            try:
                iphlpapi = ctypes.windll.iphlpapi
                AF_INET = 2
                TCP_TABLE_OWNER_PID_ALL = 5

                class MIB_TCPROW_OWNER_PID(ctypes.Structure):
                    _fields_ = [
                        ("dwState", ctypes.c_ulong),
                        ("dwLocalAddr", ctypes.c_ulong),
                        ("dwLocalPort", ctypes.c_ulong),
                        ("dwRemoteAddr", ctypes.c_ulong),
                        ("dwRemotePort", ctypes.c_ulong),
                        ("dwOwningPid", ctypes.c_ulong),
                    ]

                size = ctypes.c_ulong(0)
                iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0)
                if size.value > 0:
                    buf = ctypes.create_string_buffer(size.value)
                    if iphlpapi.GetExtendedTcpTable(buf, ctypes.byref(size), True, AF_INET, TCP_TABLE_OWNER_PID_ALL, 0) == 0:
                        num_entries = ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong)).contents.value
                        row_size = ctypes.sizeof(MIB_TCPROW_OWNER_PID)
                        offset = 4
                        ports = []
                        for _ in range(num_entries):
                            row = MIB_TCPROW_OWNER_PID.from_buffer_copy(buf[offset:offset+row_size])
                            offset += row_size
                            if row.dwOwningPid == pid and row.dwState == 2:  # MIB_TCP_STATE_LISTEN
                                port = socket.ntohs(row.dwLocalPort & 0xFFFF)
                                ports.append(port)
                        # HTTP port is typically the second/higher listening port
                        if ports:
                            return max(ports)
            except Exception:
                pass

        return None

    def query_user_status(self, port: int, csrf_token: str, timeout: float = HTTP_TIMEOUT) -> Optional[dict]:
        """Query ConnectRPC endpoint for GetUserStatus."""
        url = f"http://{self.host}:{port}{CONNECT_RPC_PATH}"
        headers = {
            "Content-Type": "application/json",
            "Connect-Protocol-Version": CONNECT_PROTOCOL_VERSION,
            "X-Codeium-Csrf-Token": csrf_token,
        }
        req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                return json.loads(data.decode("utf-8"))
        except Exception:
            return None

    def parse_quota(self, data: dict) -> Optional[Tuple[float, str]]:
        """Parse remainingFraction and resetTime from ConnectRPC response."""
        if not data or not isinstance(data, dict):
            return None
        configs = (
            data.get("userStatus", {})
            .get("cascadeModelConfigData", {})
            .get("clientModelConfigs", [])
        )
        for cfg in configs:
            quota = cfg.get("quotaInfo") or {}
            rem = quota.get("remainingFraction")
            reset_time = quota.get("resetTime")
            if rem is not None and reset_time is not None:
                try:
                    return float(rem), str(reset_time)
                except (ValueError, TypeError):
                    continue
        return None

    def get_status(self) -> dict:
        """Run full discovery and return live quota status dictionary."""
        pid = self.find_process()
        if not pid:
            return {"online": False}

        csrf = self.get_csrf_token(pid)
        if not csrf:
            return {"online": False}

        port = self.get_http_port(pid)
        if not port:
            return {"online": False}

        data = self.query_user_status(port, csrf)
        if not data:
            return {"online": False}

        quota = self.parse_quota(data)
        if not quota:
            return {"online": False}

        remaining_fraction, reset_time = quota
        return {
            "online": True,
            "remaining_fraction": remaining_fraction,
            "percentage": remaining_fraction * 100.0,
            "reset_time": reset_time,
            "countdown": format_countdown(reset_time),
        }


class LocalMetricsScanner:
    """Reads local SQLite conversation databases in read-only mode to aggregate steps and tokens."""

    def __init__(self, root_dir: Optional[Union[str, Path]] = None):
        if root_dir is not None:
            self.root_dir = Path(root_dir)
        else:
            self.root_dir = Path.home() / ".gemini" / "antigravity"

    def get_metrics(self, hours_4: float = 4.0, days_7: float = 7.0) -> dict:
        """Aggregate steps and tokens for 4-hour and 7-day windows."""
        summary_db = self.root_dir / "conversation_summaries.db"
        if not summary_db.exists():
            return {"steps_4h": 0, "tokens_4h": 0, "steps_7d": 0, "tokens_7d": 0}

        convs_4h: List[Tuple[str, int]] = []
        convs_7d: List[Tuple[str, int]] = []
        steps_4h = 0
        steps_7d = 0

        try:
            conn = sqlite3.connect(f"file:{summary_db.as_posix()}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout = 200")
            cur = conn.cursor()
            cur.execute("SELECT conversation_id, step_count, last_modified_time FROM conversation_summaries")
            rows = cur.fetchall()
            conn.close()
        except Exception:
            return {"steps_4h": 0, "tokens_4h": 0, "steps_7d": 0, "tokens_7d": 0}

        now = datetime.datetime.now(datetime.timezone.utc)
        limit_4h = hours_4 * 3600
        limit_7d = days_7 * 86400

        for cid, st, lmt in rows:
            if not lmt:
                continue
            dt = parse_iso_datetime(lmt)
            if not dt:
                continue
            age = (now - dt).total_seconds()
            steps = int(st or 0)
            if 0 <= age <= limit_4h:
                steps_4h += steps
                convs_4h.append((cid, steps))
            if 0 <= age <= limit_7d:
                steps_7d += steps
                convs_7d.append((cid, steps))

        # Scan and aggregate tokens using cached results
        tokens_4h = self.get_tokens_for_conversations(convs_4h)
        tokens_7d = self.get_tokens_for_conversations(convs_7d)

        return {
            "steps_4h": steps_4h,
            "tokens_4h": tokens_4h,
            "steps_7d": steps_7d,
            "tokens_7d": tokens_7d,
        }

    def get_tokens_for_conversations(self, convs: List[Tuple[str, int]]) -> int:
        """Scan token totals across conversations, utilizing in-memory _TOKEN_CACHE."""
        total = 0
        for cid, steps in convs:
            key = (cid, steps)
            if key in _TOKEN_CACHE:
                total += _TOKEN_CACHE[key]
                continue

            conv_db = self.root_dir / "conversations" / f"{cid}.db"
            if not conv_db.exists():
                _TOKEN_CACHE[key] = 0
                continue

            conv_tokens = 0
            try:
                conn = sqlite3.connect(f"file:{conv_db.as_posix()}?mode=ro", uri=True)
                conn.execute("PRAGMA busy_timeout = 200")
                cur = conn.cursor()
                cur.execute("SELECT data FROM gen_metadata")
                for (blob,) in cur.fetchall():
                    prompt, candidate = decode_usage_protobuf(blob)
                    conv_tokens += (prompt + candidate)
                conn.close()
            except Exception:
                conv_tokens = 0

            _TOKEN_CACHE[key] = conv_tokens
            total += conv_tokens

        return total


def get_usage_data(client: Optional[LanguageServerClient] = None,
                   scanner: Optional[LocalMetricsScanner] = None) -> dict:
    """Gather live quota and local SQLite metrics into a unified dictionary."""
    if client is None:
        client = LanguageServerClient()
    if scanner is None:
        scanner = LocalMetricsScanner()

    status = client.get_status()
    metrics = scanner.get_metrics()

    online = bool(status.get("online", False))
    return {
        "online": online,
        "remaining_fraction": float(status.get("remaining_fraction", 0.0)) if online else 0.0,
        "reset_time": str(status.get("reset_time", "")) if online else "",
        "countdown": str(status.get("countdown", "")) if online else "",
        "steps_4h": int(metrics.get("steps_4h", 0)),
        "tokens_4h": int(metrics.get("tokens_4h", 0)),
        "steps_7d": int(metrics.get("steps_7d", 0)),
        "tokens_7d": int(metrics.get("tokens_7d", 0)),
    }


def format_usage_card(data: Optional[dict] = None,
                      client: Optional[LanguageServerClient] = None,
                      scanner: Optional[LocalMetricsScanner] = None) -> str:
    """Generate clean HTML-formatted Telegram usage card (< 200ms)."""
    if data is None:
        data = get_usage_data(client=client, scanner=scanner)

    online = data.get("online", False)
    if online:
        remaining_fraction = data.get("remaining_fraction", 0.0)
        reset_time = data.get("reset_time", "")
        countdown = data.get("countdown") or format_countdown(reset_time)
        bar = make_progress_bar(remaining_fraction)
        pct = remaining_fraction * 100.0
        quota_line = f"[{bar}] {pct:.1f}% · Resets in {html.escape(countdown)}"
    else:
        quota_line = "• Status: ⚪ Offline (AntiGravity closed)"

    steps_4h = data.get("steps_4h", 0)
    tokens_4h = data.get("tokens_4h", 0)
    steps_7d = data.get("steps_7d", 0)
    tokens_7d = data.get("tokens_7d", 0)

    card = (
        "📊 <b>AI Usage &amp; Quota</b>\n\n"
        "<b>Live Quota:</b>\n"
        f"{quota_line}\n\n"
        "<b>Activity:</b>\n"
        f"• Last 4 Hours: {format_steps(steps_4h)} · {format_tokens(tokens_4h)}\n"
        f"• Last 7 Days: {format_steps(steps_7d)} · {format_tokens(tokens_7d)}"
    )
    return card
