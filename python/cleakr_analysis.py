import os
import re
import sys
import json
import subprocess
import logging
import shutil

# Logging setup
log_path = "log/cleakr.log"
logging.basicConfig(
    filename=log_path,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    force=True,
)

# OpenAI client setup
from openai import OpenAI
api_key = os.getenv("OPENAI_API_KEY")
assert api_key
client = OpenAI(api_key=api_key)
assert client
logging.info("OpenAI client initialized")

# Extracts variable name from combined message block
def extract_var_name(raw_message):
  match = re.search(r"'([^']+)'", raw_message)
  if match:
    var_name = match.group(1)
    return var_name
  return "unknown"

# Extracts leaks from clang output, groups related notes for a single warning
def extract_leaks(clang_output):
    lines = clang_output.splitlines()
    leaks = []
    current_block = []
    current_location = None
    
    pattern = re.compile(
      r"^(.*?):(\d+):(\d+):\s+(warning|note|error):\s.*?(leak|malloc|free|memory)",
      re.IGNORECASE
    )
    
    def save_current_block():
      if not current_block or not current_location:
        return
      file, line_num, col_num = current_location
      combined_msg = "\n".join(current_block)
      leak_entry = {
        "filename": file,
        "lnum": line_num - 1,  # zero-based
        "col": col_num - 1,
        "raw_message": combined_msg,
        "var_name": extract_var_name(combined_msg),
      }
      leaks.append(leak_entry)
    
    for line in lines:
      match = pattern.match(line)
      
      if not match:
        if current_block:
          current_block.append(line)
        continue

      file         = match.group(1)
      line_num     = int(match.group(2))
      col_num      = int(match.group(3))
      new_location = (file, line_num, col_num)
      
      if current_location and new_location != current_location:
        save_current_block()
        current_block = []
      
      current_location = new_location
      current_block.append(line)
    
    save_current_block()
    
    return leaks

# LLM summarizer
def summarize_leak_with_llm(leak):

  prompt = (
    "You are a helpful assistant that summarizes C memory leak warnings and gives recommendations on how to update the code to fix the memory leaks.\n"
    f"Variable involved: {leak['var_name']}\n"
    f"Warning details:\n{leak['raw_message']}\n"
    "Provide a concise summary and recommendation on how to fix the leak (max 60 chars) including variable name, severity, and leak category. Respond in plain text (no markdown formatting)."
  )
  model = "gpt-4o-mini"
  messages = [
    {"role": "user",
     "content": prompt
    }
  ]
  max_tokens = 40
  temperature = 0.3

  try:
    response = client.chat.completions.create(
      model=model,
      messages=messages,
      max_tokens=max_tokens,
      temperature=temperature
    )
    summary = response.choices[0].message.content.strip()
    return summary
  except Exception:
    logging.exception("OpenAI API call failed")
    return leak["raw_message"][:60]


# Clang-tidy runner
def run_clang_tidy(file_path):
  clang_tidy_path = shutil.which("clang-tidy")
  assert clang_tidy_path

  cmd = [
    clang_tidy_path,
    file_path,
    "--",
    "-std=c11",  # Adjust compile flags as needed
  ]
  try:
    result = subprocess.run(
      cmd,
      capture_output=True,
      text=True,
      check=False
    )
    return result.stdout
  except Exception:
    logging.error(f"Failed to run clang-tidy.")
    return ""

def main():
  assert len(sys.argv) == 2

  file_path = sys.argv[1]
  logging.info(f"Analyzing file: {file_path}")

  clang_output = run_clang_tidy(file_path)
  logging.info(f"clang output:\n{clang_output.strip()}")
  leaks = extract_leaks(clang_output)

  # if not leaks:
  #   logging.info("No leaks found")
  #   print("[]")
  #   return

  logging.info(f"Leaks: {len(leaks)}")
  diagnostics = []
  for leak in leaks:
    summary = summarize_leak_with_llm(leak)
    diagnostics.append({
      "filename": leak["filename"],
      "line": leak["lnum"],
      "col": leak["col"],
      "message": summary,
      "severity": 2,
    })
  logging.info(f"Diagnostics: {json.dumps(diagnostics)}")

  # Send back to neovim by printing to stdout
  print(json.dumps(diagnostics))

if __name__ == "__main__":
  main()