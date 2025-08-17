---@diagnostic disable: undefined-global
local api = vim.api

local M = {}

local ns = api.nvim_create_namespace("cleakr_ns")

local function clear_diagnostics(bufnr)
  api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
end

local function show_virtual_text(bufnr, diagnostics)
  clear_diagnostics(bufnr)
  
  -- Store leak data in buffer-local variable
  api.nvim_buf_set_var(bufnr, "cleakr_leak_data", diagnostics)
  
  for _, diag in ipairs(diagnostics) do
    local line = diag.line
    local text = diag.fix or ""
    api.nvim_buf_set_virtual_text(bufnr, ns, line, { { text, "WarningMsg" } }, {})
  end
end

function M.run_analysis(file_path, bufnr)
  -- Adjust if necessary
  local python_path = "/usr/bin/python3"
  local script_path = "/home/tgben/t/cleakr/python/cleakr_analysis.py"

  local stdout, stderr = "", ""

  local stdout_pipe = vim.loop.new_pipe(false)
  local stderr_pipe = vim.loop.new_pipe(false)

  local handle
  handle = vim.loop.spawn(python_path, {
    args = { script_path, file_path },
    stdio = { nil, stdout_pipe, stderr_pipe },
  }, function(code)
    stdout_pipe:close()
    stderr_pipe:close()
    handle:close()

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
      local ok, diagnostics = pcall(vim.fn.json_decode, stdout)
      if not ok then
        vim.notify("cleakr: failed to decode JSON from analysis script output", vim.log.levels.ERROR)
        vim.notify("Raw output: " .. stdout, vim.log.levels.ERROR)
        return
      end
      show_virtual_text(bufnr, diagnostics)
    end)
  end)

  stdout_pipe:read_start(function(err, data)
    if err then
      vim.schedule(function()
        vim.notify("cleakr: error reading stdout: " .. err, vim.log.levels.ERROR)
      end)
      return
    end
    if data then
      stdout = stdout .. data
    end
  end)

  stderr_pipe:read_start(function(err, data)
    if err then
      vim.schedule(function()
        vim.notify("cleakr: error reading stderr: " .. err, vim.log.levels.ERROR)
      end)
      return
    end
    if data then
      stderr = stderr .. data
    end
  end)
end

function M.clear_diagnostics(bufnr)
  clear_diagnostics(bufnr)
end

function M.show_summary()
  local current_bufnr = api.nvim_get_current_buf()
  
  -- Get stored leak data
  local ok, leak_data = pcall(api.nvim_buf_get_var, current_bufnr, "cleakr_leak_data")
  if not ok or not leak_data or #leak_data == 0 then
    vim.notify("No leak summaries available for current buffer", vim.log.levels.INFO)
    return
  end
  
  -- Create new buffer for summaries
  local summary_bufnr = api.nvim_create_buf(false, true)
  api.nvim_buf_set_name(summary_bufnr, "Cleakr Leak Summaries")
  
  -- Format the leak data
  local lines = {}
  table.insert(lines, "CLEAKR LEAK SUMMARIES")
  table.insert(lines, "=====================")
  table.insert(lines, "")
  
  for i, leak in ipairs(leak_data) do
    table.insert(lines, string.format("Leak #%d", i))
    table.insert(lines, string.format("File: %s", leak.filename))
    table.insert(lines, string.format("Line: %d, Col: %d", leak.line + 1, leak.col + 1))
    table.insert(lines, string.format("Summary: %s", leak.summary or "No summary"))
    table.insert(lines, string.format("Fix: %s", leak.fix or "No fix"))
    table.insert(lines, "")
  end
  
  -- Set buffer content
  api.nvim_buf_set_lines(summary_bufnr, 0, -1, false, lines)
  api.nvim_buf_set_option(summary_bufnr, "modifiable", false)
  api.nvim_buf_set_option(summary_bufnr, "buftype", "nofile")
  
  -- Display in current window
  api.nvim_set_current_buf(summary_bufnr)
end

function M.setup()
  api.nvim_create_autocmd("BufWritePost", {
    pattern = "*.c",
    callback = function(args)
      local bufnr = api.nvim_get_current_buf()
      M.run_analysis(args.file, bufnr)
    end,
  })

  api.nvim_create_autocmd("VimEnter", {
    callback = function()
      for _, bufnr in ipairs(api.nvim_list_bufs()) do
        if api.nvim_buf_get_option(bufnr, "filetype") == "c" then
          local file = api.nvim_buf_get_name(bufnr)
          if file ~= "" then
            M.run_analysis(file, bufnr)
          end
        end
      end
    end,
  })
  
  -- Create user command
  api.nvim_create_user_command("CleakrSummary", function()
    M.show_summary()
  end, { desc = "Show Cleakr leak summaries for current buffer" })
end

return M
