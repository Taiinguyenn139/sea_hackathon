"""Opt-in real OpenAI smoke against a disposable localhost backend.

--check-config performs no network requests or process startup. --run explicitly
permits paid model requests; no browser, shared database or port 8000 is used.
"""

import argparse
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BACKUP_MODEL")
BUSINESS_DATE = "2026-10-31"
PHONE = "0900000002"
EXPECTED_NEEDS = {
    "amount": "4000000000",
    "term": "300",
    "income": "100000000",
    "debt": "10000000",
    "property_value": "6000000000",
}
REQUIRED_BANK_TOOLS = {"get_rate", "get_active_promotions", "get_disclosures"}
ACTION_TOOLS = {"send_summary", "schedule_callback", "crm_lookup"}


class SmokeFailure(Exception):
    """Only a fixed check identifier is safe to print, never provider payloads."""


class ConfigFailure(Exception):
    def __init__(self, names):
        self.names = names


def require(condition, check):
    if not condition:
        raise SmokeFailure(check)


def read_env_file(path):
    """Parse simple KEY=value, optional export, quotes and comments, without eval.

    No variable interpolation, command substitution or multiline values are
    supported. Unknown variables are parsed but never exported to the child.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ConfigFailure(["ENV_FILE"]) from None
    values = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key):
            raise ConfigFailure(["ENV_FILE"]) from None
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError:
            raise ConfigFailure([key]) from None
        if len(tokens) > 1:
            raise ConfigFailure([key]) from None
        values[key] = tokens[0] if tokens else ""
    return values


def configuration(path):
    file_values = read_env_file(path)
    # Explicit nonempty process variables take precedence over the file.
    config = {name: os.environ.get(name) or file_values.get(name, "") for name in REQUIRED}
    missing = [name for name, value in config.items() if not value.strip()]
    if missing:
        raise ConfigFailure(missing)
    if config["OPENAI_MODEL"].strip() == config["OPENAI_BACKUP_MODEL"].strip():
        raise ConfigFailure(["OPENAI_MODEL", "OPENAI_BACKUP_MODEL"])
    return config


def api(base_url, path, *, method="GET", body=None, token=None, timeout=180):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method=method,
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise SmokeFailure("local_http_status_" + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SmokeFailure("local_http_unavailable") from None
    except (ValueError, UnicodeError):
        raise SmokeFailure("local_response_not_json") from None
    require(isinstance(result, dict), "local_response_not_object")
    return result


def validate_brief(reply):
    require(reply.get("identity_status") == "verified" and reply.get("user_id") == PHONE, "identity_not_verified")
    brief = reply.get("brief") or {}
    require(brief.get("name") == "Trần Minh An" and brief.get("user_id") == PHONE, "identity_not_an")
    require(brief.get("as_of") == BUSINESS_DATE and not brief.get("redacted", True), "brief_not_available")
    historical = [row for row in brief.get("history", []) if row.get("source_origin") == "dataset_history"]
    require(len(historical) == 9, "historical_message_count")
    sources = {row["message_id"]: row for row in historical}
    require(all(row.get("role") == "user" and row.get("user_id") == PHONE for row in historical), "historical_identity")
    needs = {fact["key"]: fact for fact in brief.get("needs", []) if fact.get("status") == "active"}
    for key, expected in EXPECTED_NEEDS.items():
        fact = needs.get(key, {})
        try:
            same = Decimal(str(fact.get("value"))) == Decimal(expected)
        except InvalidOperation:
            same = False
        require(same, "brief_needs_" + key)
        source = sources.get(fact.get("source_ref"), {})
        require(
            fact.get("source_origin") == "dataset_history" and bool(source)
            and fact.get("source_at") == source.get("at") and fact.get("channel") == source.get("channel"),
            "brief_source_" + key,
        )
    return len(historical)


def tool_records(trace):
    require(trace.get("backend") == "openai" and trace.get("business_as_of") == BUSINESS_DATE, "trace_backend_or_date")
    records = trace.get("tool_calls", [])
    require(isinstance(records, list), "trace_tool_records")
    require(not any(record.get("tool") in ACTION_TOOLS for record in records), "unexpected_action_tool")
    return records


def validate_identity_trace(trace):
    records = tool_records(trace)
    successful = [record for record in records if (
        record.get("tool") == "verify_identity" and not record.get("result", {}).get("error")
        and record.get("result", {}).get("data", {}).get("verified") is True
        and record.get("result", {}).get("data", {}).get("user_id") == PHONE
    )]
    require(bool(successful), "identity_tool_receipt_missing")
    require(all(record.get("args", {}).get("phone") == "[redacted]" for record in successful), "identity_audit_not_redacted")
    return len(successful)


def validate_bank_trace(trace, reply):
    records = tool_records(trace)
    successful = [record for record in records if not record.get("result", {}).get("error")]
    require(REQUIRED_BANK_TOOLS <= {record.get("tool") for record in successful}, "bank_tool_receipts_missing")
    issued = {}
    for record in successful:
        result = record.get("result", {})
        require(result.get("as_of") == BUSINESS_DATE and bool(result.get("tool_call_id")), "receipt_date_or_id")
        if record.get("tool") == "get_rate":
            args = record.get("args", {})
            require(
                args.get("product") == "home_loan" and args.get("segment") == "priority"
                and args.get("amount") == 4000000000 and args.get("term") == 300,
                "rate_request_not_confirmed_priority_loan",
            )
        for fact in result.get("facts", []):
            require(
                fact.get("source_type") == "bank_tool" and fact.get("source_ref") == result["tool_call_id"]
                and fact.get("tool") == record.get("tool") and fact.get("observed_at") == BUSINESS_DATE
                and fact.get("valid_until", "") >= BUSINESS_DATE and fact.get("status") == "active"
                and fact.get("channel") == "web",
                "bank_fact_provenance",
            )
            issued[fact["fact_id"]] = fact
        if record.get("tool") == "get_disclosures":
            require(bool(result.get("data", {}).get("disclosures")), "disclosures_empty")
    used = set(reply.get("used_fact_ids", []))
    standard = {key for key, fact in issued.items() if fact.get("key") == "standard_rate"}
    promotions = {key for key, fact in issued.items() if fact.get("key") == "promo_rate"}
    require(bool(used & standard), "standard_rate_not_sourced_in_reply")
    require(not promotions or bool(used & promotions), "promotion_not_sourced_in_reply")
    trace_facts = {fact.get("id", fact.get("fact_id")): fact for fact in trace.get("facts", [])}
    for key in used & issued.keys():
        require(trace_facts.get(key, {}).get("source_ref") == issued[key]["source_ref"], "reply_fact_trace_mismatch")
    return sorted({record["tool"] for record in successful}), len(issued), len(used & issued.keys())


def isolated_smoke(config):
    with tempfile.TemporaryDirectory(prefix="bankwise-agent-smoke-") as folder:
        directory = Path(folder)
        admin = secrets.token_urlsafe(32)
        env = {**os.environ, **config}
        env.update(
            APP_ENV="development", LLM_BACKEND="openai", IDENTITY_HISTORY_ENABLED="true",
            LLM_CALL_BUDGET_SECONDS="20", HARNESS_DB=str(directory / "harness.sqlite3"),
            HARNESS_RESULTS_DIR=str(directory / "results"), HARNESS_RELEASE_GATE="",
            ADMIN_TOKEN=admin, STAFF_PASSWORD=secrets.token_urlsafe(32), COOKIE_SECURE="false",
            TELEGRAM_BOT_TOKEN="", TELEGRAM_WEBHOOK_SECRET="", DEPLOY_REVISION="isolated-agent-smoke",
        )
        with (directory / "server.log").open("w", encoding="utf-8") as log, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            require(port != 8000, "unexpected_reserved_port")
            base_url = f"http://127.0.0.1:{port}"
            # Inherit the bound socket so no other process can take the port
            # between reservation and uvicorn startup.
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "harness.api:app", "--fd", str(listener.fileno()), "--no-access-log"],
                cwd=ROOT, env=env, pass_fds=(listener.fileno(),), stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    require(server.poll() is None, "backend_startup_failed")
                    try:
                        health = api(base_url, "/healthz", timeout=0.5)
                        require(health.get("backend") == "openai", "wrong_backend")
                        break
                    except SmokeFailure:
                        time.sleep(0.2)
                else:
                    raise SmokeFailure("backend_startup_timeout")
                api(base_url, "/admin/clock", method="POST", body={"as_of": BUSINESS_DATE}, token=admin)
                created = api(base_url, "/sessions", method="POST", body={"mode": "harness"})
                require(created.get("user_id") is None and created.get("identity_status") == "unverified", "session_not_anonymous")
                sid, token = created["session_id"], created["session_token"]
                identity = api(base_url, f"/sessions/{sid}/messages", method="POST", token=token, body={
                    "text": "Số điện thoại của tôi là 0900000002. Tôi muốn tiếp tục nhu cầu vay mua nhà đã trao đổi.",
                    "client_message_id": secrets.token_urlsafe(18),
                })
                history_count = validate_brief(identity)
                traces = api(base_url, f"/traces/{sid}", token=admin).get("items", [])
                require(len(traces) == 1, "identity_trace_count")
                identity_count = validate_identity_trace(traces[0])
                quote = api(base_url, f"/sessions/{sid}/messages", method="POST", token=token, body={
                    "text": (
                        "Tôi xác nhận nhu cầu hiện tại: vay mua nhà 4 tỷ trong 25 năm, thu nhập 100 triệu mỗi tháng, "
                        "đang trả nợ 10 triệu mỗi tháng, căn nhà giá 6 tỷ. Hãy lấy lãi suất chuẩn và chương trình "
                        "ưu đãi hôm nay cho kênh web theo phân khúc hồ sơ của tôi, nêu rõ cả hai mức lãi và lưu ý "
                        "điều kiện áp dụng. Tôi chỉ hỏi thông tin, không yêu cầu đặt lịch hoặc gửi tóm tắt."
                    ),
                    "client_message_id": secrets.token_urlsafe(18),
                })
                require(quote.get("identity_status") == "verified" and quote.get("user_id") == PHONE, "quote_identity_changed")
                traces = api(base_url, f"/traces/{sid}", token=admin).get("items", [])
                require(len(traces) == 2, "quote_trace_count")
                names, fact_count, used_count = validate_bank_trace(traces[-1], quote)
                return {
                    "status": "passed", "backend": "openai", "business_as_of": BUSINESS_DATE,
                    "turns": 2, "historical_messages": history_count, "identity_receipts": identity_count,
                    "bank_tools": names, "issued_bank_facts": fact_count, "used_bank_facts": used_count,
                }
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-config", action="store_true", help="Validate variable names only; no network or backend")
    mode.add_argument("--run", action="store_true", help="Explicitly opt into real, potentially paid OpenAI requests")
    args = parser.parse_args(argv)
    try:
        config = configuration(args.env_file)
        if args.check_config:
            print(json.dumps({"status": "configuration_ready", "variables": list(REQUIRED)}))
            return 0
        summary = isolated_smoke(config)
    except ConfigFailure as error:
        print("Configuration missing or invalid: " + ", ".join(error.names), file=sys.stderr)
        return 2
    except SmokeFailure as error:
        print(json.dumps({"status": "failed", "check": str(error)}), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(json.dumps({"status": "interrupted"}), file=sys.stderr)
        return 130
    except Exception:  # noqa: BLE001 -- Final CLI boundary must redact arbitrary provider/transport errors.
        # Provider messages, transport exceptions and log content can contain
        # sensitive values. Never include them in terminal output.
        print(json.dumps({"status": "failed", "check": "unexpected_local_failure"}), file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
