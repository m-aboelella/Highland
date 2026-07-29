from pathlib import Path

SERVICES = (
    "crm",
    "knowledge",
    "support",
    "observability",
    "communications",
    "projects",
)

DEFAULT_PORTS = {
    "catalog": 8099,
    "crm": 8101,
    "knowledge": 8102,
    "support": 8103,
    "observability": 8104,
    "communications": 8105,
    "projects": 8106,
}

SERVICE_PRODUCTS = {
    "crm": "Atlas CRM",
    "knowledge": "Archive",
    "support": "Relay Desk",
    "observability": "Beacon",
    "communications": "Pulse",
    "projects": "Track",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED_DIR = REPO_ROOT / "data" / "seed"
DEFAULT_RUNTIME_DIR = REPO_ROOT / "var"
