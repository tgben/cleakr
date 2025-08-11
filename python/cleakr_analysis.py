import os
import re
import sys
import json
import subprocess
import logging
import shutil  # for which()

# Logging setup (do this first!)
log_path = "cleakr.log"
logging.basicConfig(
    filename=log_path,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s: %(message)s",
    force=True,
)
logging.info("cleakr_analysis.py starting up")

# Optional OpenAI client setup
try:
    from openai import OpenAI

    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        client = OpenAI(api_key=api_key)
        logging.debug("OpenAI client initialized")
    else:
        client = None
        logging.warning("OPENAI_API_KEY not set")
except ImportError:
    client = None
    logging.warning("OpenAI module not installed")


# Helper to extract variable name from combined message block
def extract_var_name(raw_message):
  match = re.search(r"'([^']+)'", raw_message)
  if match:
    var_name = match.group(1)
    logging.debug(f"Extracted variable name: {var_name}")
    return var_name
  logging.debug("Variable name not found, returning 'unknown'")
  return "unknown"


# Leak extractor with grouping of related notes per warning
def extract_leaks(clang_output):
  lines = clang_output.splitlines()
  leaks = []
  current_block = []
  current_file = None
  current_line_num = None
  current_col_num = None

  # Match relevant clang-tidy messages for leaks/memory issues
  line_pattern = re.compile(
    r"^(.*?):(\d+):(\d+):\s+(warning|note|error):\s.*?(leak|malloc|free|memory)",
    re.IGNORECASE,
  )

  for line in lines:
    m = line_pattern.match(line)
    if m:
      file, line_num, col_num, _level, _keyword = m.groups()
      line_num = int(line_num)
      col_num = int(col_num)

      # If current block exists and we hit a new location, save the old block first
      if current_block and (file != current_file or line_num != current_line_num or col_num != current_col_num):
        combined_msg = "\n".join(current_block)
        var_name = extract_var_name(combined_msg)
        leaks.append({
          "filename": current_file,
          "lnum": current_line_num - 1,  # zero-based
          "col": current_col_num - 1,
          "raw_message": combined_msg,
          "var_name": var_name,
        })
        current_block = []

      current_file = file
      current_line_num = line_num
      current_col_num = col_num
      current_block.append(line)
    else:
      # If line doesn't match but we have a current block, treat as continuation line
      if current_block:
        current_block.append(line)

  # Add the last block if any
  if current_block:
    combined_msg = "\n".join(current_block)
    var_name = extract_var_name(combined_msg)
    leaks.append({
      "filename": current_file,
      "lnum": current_line_num - 1,
      "col": current_col_num - 1,
      "raw_message": combined_msg,
      "var_name": var_name,
    })

  logging.debug(f"Extracted leaks: {json.dumps(leaks, indent=2)}")
  return leaks


# LLM summarizer
def summarize_leak_with_llm(leak):
  if client is None:
    logging.warning("OpenAI client not available, skipping LLM summary")
    return leak["raw_message"][:80]

  prompt = (
    "You are a helpful assistant that summarizes C memory leak warnings.\n"
    f"Variable involved: {leak['var_name']}\n"
    f"Warning details:\n{leak['raw_message']}\n"
    "Provide a concise message (max 80 chars) including variable name, "
    "severity, and leak category."
  )

  try:
    response = client.chat.completions.create(
      model="gpt-4o-mini",
      messages=[{"role": "user", "content": prompt}],
      max_tokens=40,
      temperature=0.3,
    )
    summary = response.choices[0].message.content.strip()
    return summary
  except Exception:
    logging.exception("OpenAI API call failed")
    return leak["raw_message"][:80]


# Clang-tidy runner
def run_clang_tidy(file_path):
  clang_tidy_path = shutil.which("clang-tidy")
  logging.debug(f"Using clang-tidy binary at: {clang_tidy_path}")

  if not clang_tidy_path:
    logging.error("clang-tidy not found in PATH")
    return ""

  cmd = [
    clang_tidy_path,
    file_path,
    "--",
    "-std=c11",  # Adjust compile flags as needed
  ]
  logging.debug(f"Running command: {' '.join(cmd)}")
  try:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    logging.debug(f"clang-tidy stdout:\n{result.stdout.strip()}")
    logging.debug(f"clang-tidy stderr:\n{result.stderr.strip()}")
    return result.stdout
  except Exception:
    logging.exception("Failed to run clang-tidy")
    return ""


# Main
def main():
  if len(sys.argv) < 2:
    logging.error("No file path provided to analysis script")
    print("[]")
    return

  file_path = sys.argv[1]
  logging.info(f"Analyzing file: {file_path}")

  clang_output = run_clang_tidy(file_path)
  leaks = extract_leaks(clang_output)
  logging.debug(f"Raw clang-tidy output:\n{clang_output}")

  if not leaks:
    logging.info("No leaks found")
    print("[]")
    return

  diagnostics = []
  for leak in leaks:
    summary = summarize_leak_with_llm(leak)
    diagnostics.append({
      "filename": leak["filename"],
      "lnum": leak["lnum"],
      "col": leak["col"],
      "message": summary,
      "severity": 2,  # Warning
    })

  print(json.dumps(diagnostics))
  logging.info(f"Diagnostics: {json.dumps(diagnostics)}")


if __name__ == "__main__":
  main()
