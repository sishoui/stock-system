from app.api.app import create_app
from app.data.db import Database
import os

_db = Database(os.environ.get("STOCK_DB_URL", "sqlite:////data/stock.db"))
_db.create_all()
app = create_app(db=_db)
