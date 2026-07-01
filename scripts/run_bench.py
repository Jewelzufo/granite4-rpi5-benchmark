import urllib.request
import json
import time
import os
import subprocess
from datetime import datetime

# ----------------------------------------------------------------------
#  Ollama Benchmarking Script – Granite 4.0 H-350M (Q8_0) + HW Metrics
#  Model: hf.co/ibm-granite/granite-4.0-h-350m-GGUF:Q8_0
# ----------------------------------------------------------------------

def get_cpu_temp():
    """Return CPU temperature in °C (float), or None if unavailable."""
    try:
        res = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True)
        # output e.g. "temp=42.3'C"
        temp_str = res.stdout.strip().split("=")[1].split("'")[0]
        return float(temp_str)
    except Exception:
        return None

def get_throttled_status():
    """Return raw throttled bitmask string (hex) or None if unavailable."""
    try:
        res = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True)
        # output e.g. "throttled=0x0"
        return res.stdout.strip().split("=")[1]
    except Exception:
        return None

def throttled_flags_meaning(hex_str):
    """Convert throttled hex string to a dict of flag states (if possible)."""
    try:
        val = int(hex_str, 16)
    except:
        return {}
    flags = {
        "undervoltage_occurred": bool(val & 0x1),
        "arm_freq_capped": bool(val & 0x2),
        "throttled": bool(val & 0x4),
        "soft_temp_limit_active": bool(val & 0x8),
        "undervoltage_now": bool(val & 0x10000),
        "arm_freq_capped_now": bool(val & 0x20000),
        "throttled_now": bool(val & 0x40000),
        "soft_temp_limit_now": bool(val & 0x80000),
    }
    return flags

def run_benchmark(model_name, prompt):
    """Send a single prompt to the Ollama API and return metrics."""
    url = "http://localhost:11434/api/generate"

    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "repeat_penalty": 1.2,
            "num_ctx": 1028
        }
    }

    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8")

    # --- Pre‑inference hardware snapshot ---
    temp_before = get_cpu_temp()
    throttled_before = get_throttled_status()

    print("\n⏳ Processing inference via local API...")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        start_wall_time = time.time()
        with urllib.request.urlopen(req) as response:
            raw_response = response.read().decode("utf-8")
            response_data = json.loads(raw_response)
        end_wall_time = time.time()

        # --- Post‑inference hardware snapshot ---
        temp_after = get_cpu_temp()
        throttled_after = get_throttled_status()

        # --- Content & Metrics Extraction ---
        model_output = response_data.get("response", "").strip()
        eval_count = response_data.get("eval_count", 0)
        eval_duration_ns = response_data.get("eval_duration", 1)
        load_duration_ns = response_data.get("load_duration", 0)

        # Calculate Tokens Per Second (t/s)
        tokens_per_second = (eval_count / eval_duration_ns) * 1_000_000_000 if eval_duration_ns > 0 else 0

        # Build throttling interpretation for the log
        throttled_flags = throttled_flags_meaning(throttled_after) if throttled_after else {}

        print("\n💬 --- MODEL OUTPUT RESPONSE ---")
        print(model_output)
        print("--------------------------------")

        print("\n📊 --- BENCHMARK DATA ACQUIRED ---")
        print(f"🥇 Token Throughput  : {tokens_per_second:.2f} t/s")
        print(f"🔢 Output Token Count : {eval_count} tokens")
        print(f"⏱️ Compute Duration   : {eval_duration_ns / 1_000_000_000:.2f} seconds")
        if temp_before is not None:
            print(f"🌡️ CPU Temp (before)  : {temp_before}°C")
        if temp_after is not None:
            print(f"🌡️ CPU Temp (after)   : {temp_after}°C")
        if throttled_flags:
            print(f"⚠️  Throttling flags   : {throttled_flags}")
        print("---------------------------------")

        # Return structured log entry
        return {
            "query_timestamp": datetime.now().isoformat(),
            "prompt_used": prompt,
            "model_output": model_output,
            "calculated_metrics": {
                "tokens_per_second": round(tokens_per_second, 2),
                "total_tokens_generated": eval_count,
                "generation_duration_seconds": round(eval_duration_ns / 1_000_000_000, 2),
                "total_wall_clock_seconds": round(end_wall_time - start_wall_time, 2),
                "model_ram_load_seconds": round(load_duration_ns / 1_000_000_000, 2)
            },
            "hardware_metrics": {
                "cpu_temp_celsius_before": temp_before,
                "cpu_temp_celsius_after": temp_after,
                "throttled_raw_before": throttled_before,
                "throttled_raw_after": throttled_after,
                "throttled_flags_after": throttled_flags
            },
            "raw_api_output": response_data
        }

    except Exception as e:
        print(f"\n❌ Connection Error: {e}")
        print(f"Verify that '{model_name}' is loaded and running.")
        return None


def save_session_log(model_name, session_start, query_history):
    """Compile session data and write a single aggregated JSON log file."""
    if not query_history:
        print("\nℹ️ No successful queries executed. Skipping log generation.")
        return

    session_end = datetime.now()
    timestamp_str = session_start.strftime("%Y%m%d_%H%M%S")

    session_payload = {
        "session_meta": {
            "session_start_time": session_start.isoformat(),
            "session_end_time": session_end.isoformat(),
            "total_duration_seconds": round((session_end - session_start).total_seconds(), 2),
            "hardware": "Raspberry Pi 5 (8GB)",
            "model_tested": model_name,
            "global_parameters": {
                "temperature": 0.0,
                "repeat_penalty": 1.2,
                "num_ctx": 1028
            },
            "total_queries_executed": len(query_history)
        },
        "interactions": query_history
    }

    os.makedirs("bench_logs", exist_ok=True)
    safe_name = model_name.replace(":", "_").replace("/", "_").replace(".", "_")
    log_filename = f"bench_logs/session_{safe_name}_{timestamp_str}.json"

    with open(log_filename, "w") as log_file:
        json.dump(session_payload, log_file, indent=4)

    print(f"\n💾 Successfully saved entire session history ({len(query_history)} interactions) to: {log_filename}")


if __name__ == "__main__":
    TARGET_MODEL = "hf.co/ibm-granite/granite-4.0-h-350m-GGUF:Q8_0"

    # Optional: pull the model automatically (uncomment if desired)
    # os.system(f"ollama pull {TARGET_MODEL}")

    session_start_time = datetime.now()
    session_query_history = []

    print("=====================================================================")
    print(f"       Ollama Session-Wide Benchmarking Engine (Pi 5 8GB)")
    print(f"       Target Model: {TARGET_MODEL}")
    print("=====================================================================")
    print("  * Context is cleared between every interaction (via fresh request).")
    print("  * Type 'exit' or 'quit' (or hit Ctrl+C) to terminate and save.")
    print("=====================================================================\n")

    try:
        while True:
            user_input = input("Ollama-Bench >>> ").strip()

            if not user_input:
                continue

            if user_input.lower() in ['exit', 'quit']:
                print("\nShutting down benchmarking console...")
                break

            # Execute inference session
            query_metrics = run_benchmark(model_name=TARGET_MODEL, prompt=user_input)

            if query_metrics:
                session_query_history.append(query_metrics)
            print()

        # Normal exit
        save_session_log(TARGET_MODEL, session_start_time, session_query_history)
        print("Goodbye!")

    except KeyboardInterrupt:
        # Handle Ctrl+C gracefully, preserving all collected data
        print("\n\nSession interrupted via keyboard sequence. Forcing emergency save...")
        save_session_log(TARGET_MODEL, session_start_time, session_query_history)
        print("Goodbye!")
