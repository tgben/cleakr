# Cleakr.nvim

Cleakr.nvim "c-leaker" is a Neovim plugin that uses AI to analyze C source files for memory-related issues using clang-tidy and provides inline virtual text warnings of concise issue summaries and recommendations.

## Features

- Runs clang-tidy on `.c` files on save and startup.
- Extracts memory leak and related warnings from `clang-tidy` output.
- Uses OpenAI's GPT to generate concise, informative summaries.
- Displays warnings inline as clear virtual text in Neovim buffers, without cluttering your workflow.

## Requirements

- Neovim 0.7+
- Python 3 with `openai` package installed
- OpenAI API key in `OPENAI_API_KEY`

## Installation

Use your favorite plugin manager, e.g., with lazy.nvim:
```lua
-- cleakr
{
  dir = "/Users/tgbenoit/t/cleakr.nvim",
  config = function()
    require("cleakr").setup()
  end,
},
```

## Folder Structure

```
cleakr.nvim/
├── lua/
│   └── cleakr/
│       └── init.lua         # Main Lua plugin code
├── python/
│   └── cleakr_analysis.py   # Python script that runs clang-tidy and LLM API calls
├── cleakr.log               # Log file generated at runtime
├── .gitignore
└── README.md                # This readme file
```
## Demo

![Demo](demo/demo.gif)

## How It Works

1. On buffer write, (`BufWritePost`) for C files, and on Neovim startup (`VimEnter`), plugin calls the `cleakr_analysis` with the context of the edited file.
2. `cleakr_analysis` runs `clang-tidy` on the file and parses the output for memory-related warnings.
3. The clang-tidy notes are grouped and formatted to provide context to the model.
4. Variable names are extracted from the clang-tidy messages to improve the prompt.
5. The script sends a concise prompt to the OpenAI API (GPT-4o-mini model) to get a short summary (including variable names, severity, and leak category) and recommendations on how to fix it.
6. The plugin receives the diagnostics in JSON and displays the messages as virtual text in the buffer.

## Logging and Debugging

- Logs are written to `cleakr.log` in the current working directory.
- Use this file to troubleshoot clang-tidy output, OpenAI API responses, and plugin execution details.