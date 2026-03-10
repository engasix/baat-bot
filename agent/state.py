from typing import Annotated
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class State(TypedDict):
    convo:       Annotated[list[BaseMessage], add_messages]
    rag_context: str   # top-K perfume results injected into the system prompt each turn
    transfer:    bool  # True = caller is ready to order → hand off to human agent