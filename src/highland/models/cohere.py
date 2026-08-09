"""Public Cohere model adapters.

Implementation is split by SDK mapping, conversational models, and search
capabilities while this import path remains stable for callers.
"""

from .cohere_chat import CohereChatModel
from .cohere_mapping import _chat_arguments
from .cohere_search import CohereEmbeddingModel, CohereRerankModel

__all__ = [
    "CohereChatModel",
    "CohereEmbeddingModel",
    "CohereRerankModel",
    "_chat_arguments",
]
