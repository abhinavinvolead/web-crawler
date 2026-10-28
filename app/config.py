"""Environment-driven settings. No third-party dotenv dependency."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

# Verified against Bright Data docs (Sept 2026). Tripadvisor / Yelp IDs are deliberately NOT
# hardcoded: Bright Data says to look IDs up in https://docs.brightdata.com/scrapers.json.
GMAPS_REVIEWS_ID = "gd_luzfs1dn2oa0teb81"  # Google Maps reviews, collect by URL
GMAPS_PLACES_ID = "gd_m8ebnr0q2qlklc02fz"  # Google Maps full info, discover by location

DEFAULT_SEEDS = [
    {"name": "New York", "lat": 40.7128, "long": -74.0060},
    {"name": "Los Angeles", "lat": 34.0522, "long": -118.2437},
    {"name": "Chicago", "lat": 41.8781, "long": -87.6298},
    {"name": "Houston", "lat": 29.7604, "long": -95.3698},
    {"name": "Miami", "lat": 25.7617, "long": -80.1918},
]


class Settings(BaseSettings):
    api_key: str = Field(default="", validation_alias="BRIGHTDATA_API_KEY")
    # A configured API key must opt into live mode by default. Mock mode is only
    # implicit when no key is available, or when BD_MOCK=true is set explicitly.
    mock: bool = Field(default=False, validation_alias="BD_MOCK")
    db_path: str = Field(default="data/reviews.db")
    export_dir: str = Field(default="data/exports")
    poll_interval: float = Field(default=5.0, validation_alias="BD_POLL_INTERVAL")
    poll_timeout: float = Field(default=180.0, validation_alias="BD_POLL_TIMEOUT")
    max_places_hard_cap: int = Field(default=50)
    dataset_gmaps_reviews: str = Field(default=GMAPS_REVIEWS_ID, validation_alias="BD_DATASET_GMAPS_REVIEWS")
    dataset_gmaps_places: str = Field(default=GMAPS_PLACES_ID, validation_alias="BD_DATASET_GMAPS_PLACES")
    dataset_tripadvisor: str = Field(default="", validation_alias="BD_DATASET_TRIPADVISOR")
    dataset_yelp: str = Field(default="", validation_alias="BD_DATASET_YELP")
    anthropic_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-haiku-4-5-20251001")
    
    database_url: str = Field(default="", validation_alias="DATABASE_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def model_post_init(self, __context):
        if "BD_MOCK" not in os.environ and not self.api_key:
            self.mock = True
        
        if not self.database_url and self.db_path:
            self.database_url = f"sqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.db_path:
        Path(s.db_path).parent.mkdir(parents=True, exist_ok=True)
    if s.export_dir:
        Path(s.export_dir).mkdir(parents=True, exist_ok=True)
    return s
