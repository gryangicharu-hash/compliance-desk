import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compliance_core.models import Channel, Member, MemberType  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def make_member():
    def _make(no="M0001", name="Mary Wanjiru Kamau", **kw):
        base = dict(member_no=no, member_type=MemberType.INDIVIDUAL, name=name, id_number="23456789",
                    occupation="Teacher", employer="Public school (TSC)", checkoff=True,
                    channel=Channel.BRANCH, products=frozenset({"bosa_deposits", "fosa_account"}),
                    declared_monthly_income=Decimal("60000"), source_of_funds="Salary",
                    joined=date(2020, 3, 1), last_review=date(2025, 6, 1))
        base.update(kw)
        return Member(**base)
    return _make


@pytest.fixture
def un_xml():
    return (FIXTURES / "un_consolidated_sample.xml").read_bytes()
