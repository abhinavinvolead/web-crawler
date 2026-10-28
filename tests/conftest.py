import os
import tempfile

tmp = tempfile.mkdtemp()
os.environ.update({
    "BD_MOCK": "true",
    "DB_PATH": f"{tmp}/t.db",
    "EXPORT_DIR": f"{tmp}/x",
    "BD_POLL_INTERVAL": "0"
})
