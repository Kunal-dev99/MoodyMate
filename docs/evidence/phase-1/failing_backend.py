"""Real backend app whose DB ping raises, so GET /api/v1/health fails with a genuine
unhandled exception through the full middleware stack. Verification only; not shipped."""
from app.core.config import Settings
from app.main import create_app

app = create_app(Settings(ENV="local", DATABASE_URL="postgresql+asyncpg://moodymate:moodymate@localhost:5432/moodymate_ws1_p1"))


async def _boom() -> bool:
    raise RuntimeError("simulated failure for FE-1.4 verification")


app.state.db.ping = _boom
