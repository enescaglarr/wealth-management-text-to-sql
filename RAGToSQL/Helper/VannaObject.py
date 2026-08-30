import os
from pathlib import Path

from openai import OpenAI
from vanna.openai.openai_chat import OpenAI_Chat
from vanna.chromadb.chromadb_vector import ChromaDB_VectorStore

from .Credentials import Credentials
from .SqlExtract import extract_sql


class MyVanna(ChromaDB_VectorStore, OpenAI_Chat):
    """Vanna = ChromaDB vector store + Groq chat client (Groq speaks the OpenAI wire protocol)."""

    def __init__(self, config=None):
        # ChromaDB lives next to the RAGToSQL scripts regardless of the current working directory
        config = {"model": Credentials.model, "path": str(Path(__file__).resolve().parents[1]),
                  "max_tokens": 5000,  # prompt budget: keeps each request under Groq's free-tier 8k tokens/minute
                  **(config or {})}
        ChromaDB_VectorStore.__init__(self, config=config)
        client = OpenAI(api_key=Credentials.llm_api_key, base_url=Credentials.llm_base_url)
        OpenAI_Chat.__init__(self, client=client, config=config)
        self._model = config["model"]
        self.tokens_used = 0  # rough cost meter: free-tier budgets are the binding constraint
        self.last_prompt = None  # the app shows exactly what was sent to the model

    def log(self, message: str, title: str = "Info"):
        """Vanna prints the whole prompt on every call; keep it unless VANNA_VERBOSE=1."""
        if os.getenv("VANNA_VERBOSE") == "1":
            print(f"{title}: {message}")

    def submit_prompt(self, prompt, **kwargs) -> str:
        """Vanna's own submit_prompt cannot pass provider-specific options; we need `reasoning_effort`
        so gpt-oss/qwen answer with SQL instead of pages of chain-of-thought, and a completion cap."""
        self.last_prompt = prompt
        kwargs = dict(model=self._model, messages=prompt, temperature=self.temperature,
                      max_completion_tokens=800, extra_body=Credentials.extra_body())
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except Exception as e:  # provider rejected reasoning_effort -> retry without it
            if "reasoning_effort" not in str(e):
                raise
            kwargs.pop("extra_body")
            resp = self.client.chat.completions.create(**kwargs)
        if getattr(resp, "usage", None):
            self.tokens_used += resp.usage.total_tokens
        return resp.choices[0].message.content or ""

    def extract_sql(self, llm_response: str) -> str:
        """Vanna's default extractor grabs chain-of-thought text on reasoning models - see SqlExtract."""
        return extract_sql(llm_response)
