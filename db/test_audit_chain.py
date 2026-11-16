"""Integration check: entries built by compliance_core.audit are accepted by the
database's chain trigger in order, refused out of order, and can't be altered.

Usage: python db/test_audit_chain.py | psql -v ON_ERROR_STOP=1 -d <db>
(Prints SQL; uses only the standard library so it runs without a DB driver.)
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from compliance_core import audit  # noqa: E402


def lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def insert(e) -> str:
    return (f"INSERT INTO audit_log VALUES ({e.seq}, {lit(e.at)}, {lit(e.actor)}, {lit(e.action)}, "
            f"{lit(e.subject)}, {lit(json.dumps(e.detail))}::jsonb, {lit(e.prev_hash)}, {lit(e.hash)});")


chain = []
t0 = datetime(2026, 11, 16, 9, 0, tzinfo=timezone(timedelta(hours=3)))
for i, (action, subject) in enumerate([("member.onboarded", "M0043"), ("hit.cleared", "M0008"),
                                       ("str.filed", "C-0007"), ("settings.changed", "v2")]):
    audit.append(chain, at=t0 + timedelta(minutes=i), actor="co", action=action, subject=subject, detail={"n": i})
assert audit.verify(chain) is None

print("\\set QUIET on")
print(insert(chain[0]))
print(insert(chain[1]))
skip = insert(chain[3])
print(f"DO $$ BEGIN EXECUTE {lit(skip)}; RAISE EXCEPTION 'SAFEGUARD FAILED: out-of-order entry accepted'; "
      "EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE 'SAFEGUARD%' THEN RAISE; END IF; "
      "RAISE NOTICE 'ok: out-of-order audit entry refused (%)', SQLERRM; END $$;")
print(insert(chain[2]))
print(insert(chain[3]))
for stmt, label in [("UPDATE audit_log SET actor = 'someone else' WHERE seq = 2", "editing an audit entry"),
                    ("DELETE FROM audit_log WHERE seq = 4", "deleting an audit entry"),
                    ("TRUNCATE audit_log", "emptying the audit log")]:
    print(f"DO $$ BEGIN EXECUTE {lit(stmt)}; RAISE EXCEPTION 'SAFEGUARD FAILED: {label}'; "
          "EXCEPTION WHEN raise_exception THEN IF SQLERRM LIKE 'SAFEGUARD%' THEN RAISE; END IF; "
          f"RAISE NOTICE 'ok: {label} blocked (%)', SQLERRM; END $$;")
print("DO $$ BEGIN IF (SELECT count(*) FROM audit_log) <> 4 THEN RAISE EXCEPTION 'SAFEGUARD FAILED: wrong count'; END IF; "
      "RAISE NOTICE 'ok: 4 chained entries stored'; END $$;")
