---@diagnostic disable: undefined-global

local M = {}

-- Module-level constants and state
local NAMESPACE = vim.api.nvim_create_namespace("cleakr_ns")
local PYTHON_PATH = "/usr/bin/python3"
local SCRIPT_PATH = "/home/tgben/t/cleakr/python/cleakr_analysis.py"
local WINDOW_WIDTH_RATIO = 0.6
local WINDOW_HEIGHT_RATIO = 0.6

local summary_win_id = nil
local summary_buf_id = nil

-- Clear virtual text diagnostics from buffer
local function clear_diagnostics(bufnr)
  vim.api.nvim_buf_clear_namespace(bufnr, NAMESPACE, 0, -1)
end

--- Show loading ellipses on detected leak lines
---@param bufnr number Buffer number
---@param loading_data table[] Array of {line: number} objects
local function show_loading_markers(bufnr, loading_data)
  clear_diagnostics(bufnr)

  for _, item in ipairs(loading_data) do
    vim.api.nvim_buf_set_extmark(bufnr, NAMESPACE, item.line, 0, {
      virt_text = { { "...", "Comment" } },
      virt_text_pos = "eol",
    })
  end
end

--- Display final diagnostics and store leak data in buffer
---@param bufnr number Buffer number
---@param diagnostics table[] Diagnostic data
local function show_final_diagnostics(bufnr, diagnostics)
  clear_diagnostics(bufnr)
  vim.api.nvim_buf_set_var(bufnr, "cleakr_leak_data", diagnostics)

  for _, diag in ipairs(diagnostics) do
    local text = diag.fix or ""
    if text ~= "" then
      vim.api.nvim_buf_set_extmark(bufnr, NAMESPACE, diag.line, 0, {
        virt_text = { { text, "WarningMsg" } },
        virt_text_pos = "eol",
      })
    end
  end
end

-- Format leak data into display lines
local function format_leak_data(leak_data)
  local lines = {
    "CLEAKR LEAK SUMMARIES",
    "=====================",
    "",
    "Press 'q' or ':CleakrSummary' to close this window",
    ""
  }

  for i, leak in ipairs(leak_data) do
    table.insert(lines, string.format("Leak #%d", i))
    table.insert(lines, string.format("File: %s", leak.filename))
    table.insert(lines, string.format("Line: %d, Col: %d", leak.line + 1, leak.col + 1))
    table.insert(lines, string.format("Summary: %s", leak.summary or "No summary"))
    table.insert(lines, string.format("Fix: %s", leak.fix or "No fix"))
    table.insert(lines, "")
  end

  return lines
end

-- Get or create summary buffer
local function get_summary_buffer()
  if summary_buf_id and vim.api.nvim_buf_is_valid(summary_buf_id) then
    return summary_buf_id
  end

  local bufnr = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_name(bufnr, "Cleakr Leak Summaries")
  summary_buf_id = bufnr

  return bufnr
end

-- Create floating window configuration
local function create_window_config()
  local width = math.floor(vim.o.columns * WINDOW_WIDTH_RATIO)
  local height = math.floor(vim.o.lines * WINDOW_HEIGHT_RATIO)
  local row = math.floor((vim.o.lines - height) / 2)
  local col = math.floor((vim.o.columns - width) / 2)

  return {
    relative = "editor",
    width = width,
    height = height,
    row = row,
    col = col,
    style = "minimal",
    border = "rounded"
  }
end

--- Parse streaming output from analysis script
---@param line string Output line to parse
---@param bufnr number Buffer number
local function process_output_line(line, bufnr)
  -- Handle loading state
  local loading_match = line:match("^LOADING: (.+)$")
  if loading_match then
    local ok, loading_data = pcall(vim.fn.json_decode, loading_match)
    if ok and loading_data and type(loading_data) == "table" then
      show_loading_markers(bufnr, loading_data)
    else
      vim.notify("Failed to parse loading data", vim.log.levels.WARN)
    end
    return
  end

  -- Handle final results
  local final_match = line:match("^FINAL: (.+)$")
  if final_match then
    local ok, diagnostics = pcall(vim.fn.json_decode, final_match)
    if ok and diagnostics and type(diagnostics) == "table" then
      show_final_diagnostics(bufnr, diagnostics)
    else
      vim.notify("Failed to parse final diagnostics", vim.log.levels.WARN)
    end
    return
  end
end

-- Handle analysis completion
local function handle_analysis_result(code, stdout, stderr, bufnr)
  if code ~= 0 then
    vim.schedule(function()
      vim.notify(
        string.format("cleakr_analysis.py exited with code %d\nstderr: %s", code, stderr),
        vim.log.levels.ERROR
      )
    end)
    return
  end

  vim.schedule(function()
    -- Process each line of output for LOADING/FINAL messages
    for line in stdout:gmatch("[^\r\n]+") do
      process_output_line(line, bufnr)
    end
  end)
end

--- Extract complete lines from streaming data
---@param data string Raw streaming data
---@return string[] lines Complete lines
---@return string remaining Remaining partial data
local function extract_lines(data)
  local lines = {}
  local remaining = data

  while true do
    local newline_pos = remaining:find("\n")
    if not newline_pos then
      break
    end

    local line = remaining:sub(1, newline_pos - 1)
    if line ~= "" then
      table.insert(lines, line)
    end
    remaining = remaining:sub(newline_pos + 1)
  end

  return lines, remaining
end

--- Set up pipe reader with real-time line processing
---@param pipe userdata Pipe handle
---@param output_var table Output accumulator
---@param error_prefix string Error message prefix
---@param bufnr? number Buffer number (only for stdout)
local function setup_pipe_reader(pipe, output_var, error_prefix, bufnr)
  pipe:read_start(function(err, data)
    if err then
      vim.schedule(function()
        vim.notify(
          string.format("cleakr: error reading %s: %s", error_prefix, err),
          vim.log.levels.ERROR
        )
      end)
      return
    end

    if not data then
      return
    end

    output_var[1] = output_var[1] .. data

    -- Process stdout lines in real-time
    if error_prefix == "stdout" and bufnr then
      local lines, remaining = extract_lines(output_var[1])
      output_var[1] = remaining

      for _, line in ipairs(lines) do
        vim.schedule(function()
          process_output_line(line, bufnr)
        end)
      end
    end
  end)
end

-- Run clang analysis on file
function M.run_analysis(file_path, bufnr)
  local stdout = { "" }
  local stderr = { "" }

  local stdout_pipe = vim.loop.new_pipe(false)
  local stderr_pipe = vim.loop.new_pipe(false)

  local handle
  handle = vim.loop.spawn(PYTHON_PATH, {
    args = { SCRIPT_PATH, file_path },
    stdio = { nil, stdout_pipe, stderr_pipe },
  }, function(code)
    stdout_pipe:close()
    stderr_pipe:close()
    handle:close()
    handle_analysis_result(code, stdout[1], stderr[1], bufnr)
  end)

  setup_pipe_reader(stdout_pipe, stdout, "stdout", bufnr)
  setup_pipe_reader(stderr_pipe, stderr, "stderr")
end

-- Clear diagnostics for buffer
function M.clear_diagnostics(bufnr)
  clear_diagnostics(bufnr)
end

-- Toggle summary window display
function M.show_summary()
  -- Close window if already open
  if summary_win_id and vim.api.nvim_win_is_valid(summary_win_id) then
    vim.api.nvim_win_close(summary_win_id, true)
    summary_win_id = nil
    return
  end

  local current_bufnr = vim.api.nvim_get_current_buf()

  -- Get stored leak data
  local ok, leak_data = pcall(vim.api.nvim_buf_get_var, current_bufnr, "cleakr_leak_data")
  if not ok or not leak_data or #leak_data == 0 then
    vim.notify("No leak summaries available for current buffer", vim.log.levels.INFO)
    return
  end

  -- Prepare buffer and content
  local summary_bufnr = get_summary_buffer()
  local lines = format_leak_data(leak_data)

  -- Update buffer content
  vim.api.nvim_buf_set_option(summary_bufnr, "modifiable", true)
  vim.api.nvim_buf_set_lines(summary_bufnr, 0, -1, false, lines)
  vim.api.nvim_buf_set_option(summary_bufnr, "modifiable", false)
  vim.api.nvim_buf_set_option(summary_bufnr, "buftype", "nofile")

  -- Create and display floating window
  local win_config = create_window_config()
  summary_win_id = vim.api.nvim_open_win(summary_bufnr, true, win_config)

  -- Set up key mapping for closing
  vim.api.nvim_buf_set_keymap(
    summary_bufnr,
    "n",
    "q",
    "<cmd>lua require('cleakr').show_summary()<CR>",
    { noremap = true, silent = true }
  )
end

-- Initialize plugin
function M.setup()
  -- Auto-analyze C files on save
  vim.api.nvim_create_autocmd("BufWritePost", {
    pattern = "*.c",
    callback = function(args)
      local bufnr = vim.api.nvim_get_current_buf()
      M.run_analysis(args.file, bufnr)
    end,
  })

  -- Auto-analyze C files on Neovim startup
  vim.api.nvim_create_autocmd("VimEnter", {
    callback = function()
      for _, bufnr in ipairs(vim.api.nvim_list_bufs()) do
        if vim.api.nvim_buf_get_option(bufnr, "filetype") == "c" then
          local file = vim.api.nvim_buf_get_name(bufnr)
          if file ~= "" then
            M.run_analysis(file, bufnr)
          end
        end
      end
    end,
  })

  -- Create user command
  vim.api.nvim_create_user_command("CleakrSummary", function()
    M.show_summary()
  end, { desc = "Show Cleakr leak summaries for current buffer" })
end

return M