import os
import re
import sys
import json
import subprocess
import logging
import shutil
from typing import NoReturn, TYPE_CHECKING

def fail(msg: str) -> NoReturn:
  sys.exit(msg)

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
if not api_key:
  fail(f"OPENAI_API_KEY not found")
client = OpenAI(api_key=api_key)
if not client:
  fail(f"Failed to initialize OpenAI client")
logging.info("OpenAI client initialized")

# Extracts variable name from combined message block
def extract_var_name(raw_message):
  patterns = [
    # Quoted variable name (from warning messages)
    r"'([^']+)'",
    # Variable declaration with memory allocation
    r"\w+\s*\*?\s*(\w+)\s*=\s*(malloc|calloc|realloc)",
    # General variable assignment
    r"(\w+)\s*=\s*\w+",
  ]
  
  for pattern in patterns:
    match = re.search(pattern, raw_message)
    if match:
      return match.group(1)
  
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
    seen_lines = set()  # Track line numbers to avoid duplicates
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
      line_key = (file, line_num - 1)  # Use zero-based line number for consistency
      
      if line_key in seen_lines:
        return
        
      seen_lines.add(line_key)
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

# Batch LLM summarizer for all leaks
def summarize_all_leaks_with_llm(leaks):
  if not leaks:
    return []
    
  max_chars = 60
  
  # Build prompt with all leaks
  leak_descriptions = []
  for i, leak in enumerate(leaks, 1):
    leak_desc = (
      f"Leak #{i}:\n"
      f"  Variable: {leak['var_name']}\n"
      f"  Details: {leak['raw_message']}\n"
      f"  AST context: {leak.get('ast_context', 'No AST context')}\n"
    )
    leak_descriptions.append(leak_desc)
  
  prompt = (
    "Analyze these C memory leaks and provide summaries and fix recommendations for each.\n\n"
    + "\n".join(leak_descriptions) + "\n\n"
    'Respond with ONLY valid JSON array, no formatting or code blocks: [{"summary": "<brief technical summary>", "fix": "Leak: <variable-name>; Rec: <recommendation>."}]\n\n'
    f"Keep each recommendation under {max_chars} chars, unless you need more for valid json formatting. No warnings, severity, or categories. "
    f"Return exactly {len(leaks)} objects in the array, one for each leak in order. "
    "DO NOT wrap in markdown code blocks."
  )
  
  model = "gpt-4o-mini"
  messages = [
    {"role": "user",
     "content": prompt
    }
  ]
  max_tokens_per_leak = 100  # Increased for JSON overhead and batching
  max_tokens = max(200, max_tokens_per_leak * len(leaks))  # Minimum 200 tokens
  temperature = 0.3
  logging.info(prompt)

  response = client.chat.completions.create(
    model=model,
    messages=messages,
    max_tokens=max_tokens,
    temperature=temperature
  )
  response = response.choices[0].message.content.strip()
  response_json = json.loads(response)

  if len(response_json) != len(leaks):
    fail(f"Response len is not the same length as the number of leaks. {response_json}")
  
  results = []
  for result in response_json:
    summary = result["summary"]
    fix = result["fix"]
    if not summary:
      fail(f"LLM did not return a valid summary. {result}")
    if not fix:
      fail(f"LLM did not return a valid fix. {result}")
    results.append((summary, fix))
  
  return results


# Clang-tidy runner
def run_clang_tidy(file_path):
  clang_tidy_path = shutil.which("clang-tidy")
  if not clang_tidy_path:
    fail(f"clang-tidy not found.")

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
  if not clang_path:
    fail(f"clang not found.")

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
  if len(sys.argv) != 2:
    fail(f"Incorrect number of args. Are you running this directly? Expects a path to a .c file.")

  file_path = sys.argv[1]
  logging.info(f"Analyzing file: {file_path}")

  clang_output = run_clang_tidy(file_path)
  logging.info(f"clang output:\n{clang_output.strip()}")
  ast_output = run_clang_ast(file_path)
  leaks = extract_leaks(clang_output, ast_output)

  logging.info(f"Leaks: {len(leaks)}")
  
  # Output loading state for immediate UI feedback
  if leaks:
    loading_data = [{"line": leak["lnum"]} for leak in leaks]
    print(f"LOADING: {json.dumps(loading_data)}")
    sys.stdout.flush()
  
  summaries_and_fixes = summarize_all_leaks_with_llm(leaks) if leaks else []
  
  diagnostics = []
  for leak, (summary, fix) in zip(leaks, summaries_and_fixes):
    diagnostics.append({
      "filename": leak["filename"],
      "line": leak["lnum"],
      "col": leak["col"],
      "summary": summary,
      "fix": fix,
    })
  logging.info(f"Diagnostics: {json.dumps(diagnostics, indent=2)}")

  # Send final diagnostics back to neovim
  print(f"FINAL: {json.dumps(diagnostics)}")
  sys.stdout.flush()

if __name__ == "__main__":
  main()