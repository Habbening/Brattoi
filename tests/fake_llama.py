#!/usr/bin/env python3
"""Stand-in for llama-completion in the tests: same prompts and end marks, canned words."""
import sys

args = sys.argv[1:]
if "-st" in args:  # one help answer
    sys.stdout.write("Put **BIOS** files in `MUOS/bios`.\n- one\n- two\n[end of text]\n")
    sys.exit(0)
sys.stdout.write("\n> ")
sys.stdout.flush()
think = "<think>\n\n</think>\n\n"  # what Qwen3.5 prints with thinking off
for turn, line in enumerate(sys.stdin, 1):
    if turn == 3:  # the context is full: llama.cpp stops
        sys.exit(0)
    sys.stdout.write(think + "Reply %d to: %s\n\n> " % (turn, line.strip()))
    sys.stdout.flush()
