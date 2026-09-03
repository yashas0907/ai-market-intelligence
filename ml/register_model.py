"""Register trained model metadata into the platform DB (MLOps lineage)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import ModelMetadataDB  # noqa: E402


async def register(summary: dict) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    db_path = Path(__file__).resolve().parents[1] / "backend" / "market_intel.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        s.add(
            ModelMetadataDB(
                model_name=summary["model_name"],
                version=summary["model_version"],
                task=summary["task"],
                trained_at=datetime.now(timezone.utc),
                train_period=summary["train_period"],
                test_period=summary["test_period"],
                metrics={"val": summary["candidates"][summary["model_name"]], "test": summary["test_metrics"], "majority_baseline_test": summary["majority_baseline_test"]},
                params={"feature_cols": summary.get("feature_cols"), "thresholds": summary["thresholds"]},
                artifact_path=summary.get("artifact_path"),
                notes=summary["methodology"] + " LIMITATIONS: " + summary["limitations"],
            )
        )
        await s.commit()
    await engine.dispose()
    print(f"registered {summary['model_name']} v{summary['model_version']} ({summary['task']})")


if __name__ == "__main__":
    import asyncio
    import json

    summary_file = sys.argv[1] if len(sys.argv) > 1 else "ml/artifacts/summary_aapl.json"
    with open(summary_file) as f:
        summary = json.load(f)
    asyncio.run(register(summary))
