import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for minimal policy-only installs
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(Path.cwd() / ".env", override=False)


@dataclass
class Settings:
    data_dir: Path | None = None
    graph_backend: str | None = None
    graph_cases_path: Path | None = None
    model: str | None = None
    embedding_model: str | None = None
    tigergraph_mcp_url: str | None = None
    tigergraph_graph_name: str | None = None
    read_only: bool | None = None
    database_url: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str | None = None

    def __post_init__(self) -> None:
        self.data_dir = self.data_dir or Path(os.getenv("HHGOA_DATA_DIR", Path(__file__).resolve().parents[1]))
        self.graph_backend = self.graph_backend or os.getenv("HHGOA_GRAPH_BACKEND", "local")
        if self.graph_cases_path is None and os.getenv("HHGOA_GRAPH_CASES_PATH"):
            self.graph_cases_path = Path(os.environ["HHGOA_GRAPH_CASES_PATH"])
        self.model = self.model or os.getenv("HHGOA_MODEL", "openrouter/free")
        self.embedding_model = self.embedding_model or os.getenv("HHGOA_EMBEDDING_MODEL", "text-embedding-3-small")
        self.tigergraph_mcp_url = self.tigergraph_mcp_url or os.getenv("HHGOA_TIGERGRAPH_MCP_URL", "http://localhost:8001/mcp/")
        self.tigergraph_graph_name = self.tigergraph_graph_name or os.getenv("HHGOA_TIGERGRAPH_GRAPH_NAME", os.getenv("TG_GRAPHNAME", "FraudGraph"))
        if self.read_only is None:
            self.read_only = os.getenv("HHGOA_READ_ONLY", "true").lower() == "true"
        self.database_url = self.database_url or os.getenv("HHGOA_DATABASE_URL")
        self.openrouter_api_key = self.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
        self.openrouter_base_url = self.openrouter_base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    @property
    def cases_path(self) -> Path:
        return self.graph_cases_path or self.data_dir / "runtime" / "cases.jsonl"


settings = Settings()
