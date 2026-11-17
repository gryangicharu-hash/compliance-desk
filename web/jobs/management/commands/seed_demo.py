"""Fill an empty database with fictitious demo data for training and demonstrations.

Everything is invented: names, ID numbers, the sanctions list. Data goes in through
the same import code as real core banking files, so screening, risk rating, the
guarantor network and monitoring all run on it. Refuses to run on a database that
already has members.
"""
import csv
import io
import random
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Role, User
from accounts.security import db_role
from accounts.staff_views import one_time_password
from corebanking.services import import_file
from jobs.services import system_user
from members.models import Member
from screening.services import load_list

FIRST = ["Wanjiru", "Kamau", "Achieng", "Otieno", "Njeri", "Kiprotich", "Atieno", "Mwangi", "Chebet", "Wafula",
         "Nyambura", "Omondi", "Jepkosgei", "Mutua", "Akinyi", "Kariuki", "Moraa", "Kiplagat", "Wairimu", "Barasa"]
LAST = ["Kamau", "Odhiambo", "Mwangi", "Kipchoge", "Wanjala", "Mutiso", "Nyaga", "Onyango", "Cheruiyot", "Gitau",
        "Owino", "Kibet", "Muthoni", "Ndungu", "Okoth", "Rotich", "Waweru", "Kimani", "Ochieng", "Mugo"]
JOBS = [("Teacher", 65000, True), ("Nurse", 70000, True), ("Clerk", 40000, True), ("Farmer", 30000, False),
        ("Trader", 80000, False), ("Driver", 35000, False), ("Police officer", 60000, True), ("Mechanic", 45000, False)]


def _csv(header, rows) -> bytes:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode()


class Command(BaseCommand):
    help = "Load fictitious demo data into an EMPTY database (training and demonstrations only)."

    def add_arguments(self, parser):
        parser.add_argument("--yes-this-is-a-demo", action="store_true", dest="confirmed",
                            help="Required: confirms this is not a production database")
        parser.add_argument("--with-users", action="store_true", help="Also create demo staff accounts")
        parser.add_argument("--members", type=int, default=150)

    def handle(self, *args, confirmed=False, with_users=False, members=150, **opts):
        if not confirmed:
            raise CommandError("Add --yes-this-is-a-demo to confirm this is not a production database.")
        if members < 50:
            raise CommandError("Use at least 50 members (the demo scenarios need member numbers up to D0045).")
        if Member.objects.exists():
            raise CommandError("This database already has members; demo data is only loaded into an empty one.")
        rng = random.Random(2026)
        now = timezone.localtime()
        sysuser = system_user()
        with db_role("system"):
            fixture = settings.BASE_DIR.parent / "tests" / "fixtures" / "un_consolidated_sample.xml"
            load_list(data=fixture.read_bytes(), filename=fixture.name, list_name="Demo sanctions list (fictitious)",
                      kind="sanctions", fmt="un_xml", user=sysuser)

            rows = []
            for i in range(1, members + 1):
                job, income, checkoff = rng.choice(JOBS)
                name = f"{rng.choice(FIRST)} {rng.choice(FIRST)} {rng.choice(LAST)}"
                joined = now.date() - timedelta(days=rng.randint(60, 3000))
                rows.append([f"D{i:04d}", name, f"{30000000 + i * 7919}", "individual", job,
                             income + rng.randint(-5, 5) * 1000, "yes" if checkoff else "no", "Salary" if checkoff else "Business",
                             "yes" if rng.random() > 0.08 else "no", joined.isoformat()])
            rows[4][1], rows[4][4] = "Marcus Hale Dunmore", "Trader"                  # matches the fictitious list
            rows[7][4] = "Forex dealer"
            rows.append([f"D{members + 1:04d}", "Umoja Women Group", "SHG/2019/0442", "group", "", 120000, "no",
                         "Member contributions", "yes", (now.date() - timedelta(days=900)).isoformat()])
            b = import_file(data=_csv(["member_no", "name", "id_number", "member_type", "occupation", "monthly_income",
                                       "checkoff", "source_of_funds", "kyc_complete", "joined_on"], rows),
                            filename="demo-members.csv", kind="members", user=sysuser)
            self._check(b)

            loans, n = [], 0
            for i in range(1, 61):
                n += 1
                borrower = f"D{rng.randint(1, members):04d}"
                gs = rng.sample([f"D{j:04d}" for j in range(1, members + 1) if f"D{j:04d}" != borrower], rng.randint(1, 3))
                amount = rng.randint(5, 50) * 10000
                for g in gs:
                    loans.append([f"LN{n:05d}", borrower, amount, g, amount // len(gs)])
            loans += [["LN09001", "D0010", 200000, "D0011", 200000], ["LN09002", "D0011", 180000, "D0010", 180000]]
            for k in range(6):                                                         # one member guarantees many
                loans.append([f"LN0910{k}", f"D{40 + k:04d}", 100000, "D0020", 50000])
            self._check(import_file(data=_csv(["loan_no", "borrower_no", "loan_amount", "guarantor_no", "guaranteed_amount"], loans),
                                    filename="demo-loans.csv", kind="loans", user=sysuser))

            txns, t = [], 0

            def add(member, days_ago, amount, direction, channel, counterparty="", purpose="deposit"):
                nonlocal t
                t += 1
                at = now - timedelta(days=days_ago, hours=rng.randint(0, 8))
                txns.append([f"TX{t:07d}", member, at.strftime("%Y-%m-%d %H:%M"), amount, direction, channel, counterparty, purpose])

            for i in range(1, members + 1):
                for d in (55, 25):
                    add(f"D{i:04d}", d, rng.randint(2, 9) * 1000, "in", "checkoff", "", "deposit")
                if rng.random() < 0.4:
                    add(f"D{i:04d}", rng.randint(1, 50), rng.randint(1, 20) * 500, "in", "mobile_money", f"07{rng.randint(10**7, 10**8 - 1)}")
            for d, amt in ((6, 1450000), (5, 1500000), (4, 1400000)):                 # structuring
                add("D0030", d, amt, "in", "cash")
            add("D0031", 3, 2400000, "in", "cash")                                     # large cash
            add("D0032", 10, 900000, "in", "bank_transfer", "ACC-118822")              # far above income
            add("D0033", 2, 800000, "in", "bank_transfer", "ACC-559100")               # in and straight out
            add("D0033", 1, 760000, "out", "mobile_money", "0722000111", "withdrawal")
            for k in range(7):                                                          # many payers
                add("D0034", 20 - k, 15000, "in", "mobile_money", f"07330000{k:02d}", "loan_repayment")
            self._check(import_file(data=_csv(["txn_id", "member_no", "date_time", "amount", "direction", "channel",
                                               "counterparty", "purpose"], txns),
                                    filename="demo-transactions.csv", kind="transactions", user=sysuser))

        self.stdout.write(self.style.SUCCESS(f"Demo data loaded: {members + 1} members, {len(loans)} guarantee rows, "
                                             f"{len(txns)} transactions. All names and numbers are fictitious."))
        if with_users:
            self.stdout.write("Demo staff (one-time passwords, change at first login):")
            for username, role in (("demo.admin", Role.ADMINISTRATOR), ("demo.co", Role.COMPLIANCE_OFFICER),
                                   ("demo.ceo", Role.SENIOR_MANAGEMENT), ("demo.auditor", Role.AUDITOR),
                                   ("demo.teller", Role.FRONT_OFFICE)):
                if User.objects.filter(username=username).exists():
                    continue
                pw = one_time_password()
                User.objects.create_user(username, password=pw, role=role, must_change_password=True)
                self.stdout.write(f"  {username:14} {role:20} {pw}")

    def _check(self, batch):
        if batch.status != "imported":
            raise CommandError(f"{batch.source} was rejected: {batch.errors[:5]}")
