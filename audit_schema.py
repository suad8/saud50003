"""هل ينتج «alembic upgrade head» نفس ما تصفه النماذج؟"""
import os, subprocess, tempfile, pathlib
os.environ["DAIF_SECRET_KEY"] = "audit"
from sqlalchemy import create_engine, inspect
from daif.models import Base

tmp = tempfile.mkdtemp()
mig_url = f"sqlite:///{tmp}/mig.db"
mod_url = f"sqlite:///{tmp}/mod.db"

env = dict(os.environ, DAIF_DATABASE_URL=mig_url)
r = subprocess.run([".venv/bin/alembic", "upgrade", "head"], capture_output=True, text=True, env=env, cwd=".")
print("alembic rc:", r.returncode)
if r.returncode: print(r.stdout[-1500:], r.stderr[-2000:]); raise SystemExit(1)

create_engine(mod_url).dispose()
e2 = create_engine(mod_url); Base.metadata.create_all(e2)
mi, mo = inspect(create_engine(mig_url)), inspect(e2)

tm, tb = set(mi.get_table_names()), set(mo.get_table_names())
tb.discard("alembic_version"); tm.discard("alembic_version")
if tm - tb: print("‼ جداول في الترحيلات وليست في النماذج:", sorted(tm - tb))
if tb - tm: print("‼ جداول ناقصة من الترحيلات:", sorted(tb - tm))

bad = 0
for t in sorted(tb & tm):
    cm = {c["name"] for c in mi.get_columns(t)}
    cb = {c["name"] for c in mo.get_columns(t)}
    if cb - cm:
        print(f"‼ {t}: أعمدة ناقصة من الترحيلات -> {sorted(cb - cm)}"); bad += 1
    if cm - cb:
        print(f"· {t}: أعمدة زائدة في الترحيلات -> {sorted(cm - cb)}")
print("\nجداول متطابقة:" if not bad and not (tb - tm) else "\n‼ يوجد انحراف", len(tb & tm))
