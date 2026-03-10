"""
Baat Bot LangGraph — compiled agent graph.

Flow:
  START → assistant ──► (transfer=False) ──► assistant   (Q&A loop)
                    └──► (transfer=True)  ──► transfer → END
"""

from langgraph.graph import END, START, StateGraph

from agent.nodes import assistant_node, transfer_node
from agent.state import State


def _route(state: State) -> str:
    return "transfer" if state.get("transfer") else END


graph = StateGraph(State)
graph.add_node("assistant", assistant_node)
graph.add_node("transfer",  transfer_node)

graph.add_edge(START, "assistant")
graph.add_conditional_edges("assistant", _route, {"transfer": "transfer", END: END})
graph.add_edge("transfer", END)

app = graph.compile()
