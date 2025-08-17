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

# Extracts AST context for a variable at a specific line
def extract_ast_context(ast_output, line_num, var_name):
  if not ast_output:
    return "No AST context"
  
  lines = ast_output.splitlines()
  context_info = []
  line_target = f"line:{line_num}"
  allocation_funcs = ["malloc", "calloc", "free"]
  
  def is_function_decl(line):
    return "FunctionDecl" in line and "'" in line
    
  def is_var_decl(line):
    return "VarDecl" in line and var_name in line and "'" in line
    
  def is_allocation_call(line):
    return "CallExpr" in line and any(func in line for func in allocation_funcs)
  
  for line in lines:
    if line_target not in line:
      continue
      
    if is_function_decl(line):
      func_name = line.split("'")[1]
      context_info.append(f"function: {func_name}")
    
    elif is_var_decl(line):
      parts = line.split("'")
      if len(parts) > 2:
        var_type = parts[-2]
        context_info.append(f"type: {var_type}")
    
    elif is_allocation_call(line):
      context_info.append("allocation call found")
  
  return "; ".join(context_info) if context_info else "Basic AST info available"

# Extracts leaks from clang output, groups related notes for a single warning
def extract_leaks(clang_output, ast_output):
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
      var_name = extract_var_name(combined_msg)
      ast_context = extract_ast_context(ast_output, line_num, var_name)
      leak_entry = {
        "filename": file,
        "lnum": line_num - 1,  # zero-based
        "col": col_num - 1,
        "raw_message": combined_msg,
        "var_name": var_name,
        "ast_context": ast_context,
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
  max_chars = 60
  prompt = (
    "Analyze this C memory leak and provide a fix recommendation.\n\n"
    f"Variable: {leak['var_name']}\n"
    f"Leak details: {leak['raw_message']}\n"
    f"AST context: {leak.get('ast_context', 'No AST context')}\n\n"
    f"Respond in this exact format: 'Leak: <variable-name>; Rec: <recommendation>.'\n"
    f"Keep recommendation under {max_chars} chars. No warnings, severity, or categories."
  )
  model = "gpt-4o-mini"
  messages = [
    {"role": "user",
     "content": prompt
    }
  ]
  max_tokens = 40
  temperature = 0.3
  logging.info(prompt)

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
    return leak["raw_message"][:max_chars]


# Clang-tidy runner
def run_clang_tidy(file_path):
  clang_tidy_path = shutil.which("clang-tidy")
  assert clang_tidy_path

  cmd = [
    clang_tidy_path,
    file_path,
    "--",
    "-std=c11",
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

# Clang AST dump runner
def run_clang_ast(file_path):
  clang_path = shutil.which("clang")
  assert clang_path

  cmd = [
    clang_path,
    "-Xclang",
    "-ast-dump",
    "-fsyntax-only",
    file_path,
    "-std=c11",
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
    logging.error(f"Failed to run clang ast-dump.")
    return ""

def main():
  assert len(sys.argv) == 2

  file_path = sys.argv[1]
  logging.info(f"Analyzing file: {file_path}")

  clang_output = run_clang_tidy(file_path)
  logging.info(f"clang output:\n{clang_output.strip()}")
  ast_output = run_clang_ast(file_path)
  leaks = extract_leaks(clang_output, ast_output)

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

  # Send diagnostics back to neovim by printing to stdout
  print(json.dumps(diagnostics))

if __name__ == "__main__":
  main()