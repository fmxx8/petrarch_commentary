from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_COMMENTARIES_DIR = DATA_DIR / "raw" / "commentaries"
RAW_CANZONIERE_DIR = DATA_DIR / "raw" / "canzoniere"
PROCESSED_DIR = DATA_DIR / "processed"
PAGES_DIR = PROCESSED_DIR / "pages"
COMMENTARIES_DIR = PROCESSED_DIR / "commentaries"
REVIEW_DIR = DATA_DIR / "review"
CANZONIERE_JSON = PROCESSED_DIR / "canzoniere.json"
DB_PATH = PROCESSED_DIR / "petrarch.db"
SCHEMA_DIR = PROJECT_ROOT / "schema"
