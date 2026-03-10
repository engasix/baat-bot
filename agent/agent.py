"""
Standalone terminal test for the Baat Bot agent.

Run:  uv run python agent/agent.py

Type your question (Urdu or English), press Enter.
Type 'exit' or 'quit' to stop.
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so 'agent', 'rag', etc. resolve correctly
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage

from agent import app, warmup
from agent.state import State


def main() -> None:
    print("=" * 60)
    print("  Baat Bot — Agent Test (Terminal Mode)")
    print("  Type your question in Urdu. Type 'exit' to quit.")
    print("=" * 60)

    warmup()

    # Initial state
    state: State = {
        "convo":       [],
        "rag_context": "",
        "transfer":    False,
    }

    while True:
        try:
            user_input = input("آپ: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[خدا حافظ]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "خدا حافظ"):
            print("عائشہ: خدا حافظ! Pure Scents کال کرنے کا شکریہ۔")
            break

        # Add user message to state and invoke one turn
        state["convo"] = state["convo"] + [HumanMessage(content=user_input)]
        state = app.invoke(state)

        # Print agent reply
        last_reply = state["convo"][-1].content
        print(f"\nعائشہ: {last_reply}\n")

        # Check if call should transfer
        if state.get("transfer"):
            print("─" * 60)
            print("  [TRANSFER] Call is being transferred to human agent.")
            print("─" * 60)
            break

        # Reset transfer flag for next turn (keep conversation history)
        state["transfer"] = False


if __name__ == "__main__":
    main()
