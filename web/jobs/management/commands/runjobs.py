from django.core.management.base import BaseCommand, CommandError

from jobs.models import JobRun
from jobs.services import JOBS, NIGHTLY, run_job


class Command(BaseCommand):
    help = ("Run scheduled jobs. 'nightly' runs: " + ", ".join(NIGHTLY) +
            ". Schedule 'fetch_un_list' daily and 'nightly' every night (see docs/OPERATIONS.md).")

    def add_arguments(self, parser):
        parser.add_argument("jobs", nargs="+", help="Job names, or 'nightly'. Choices: " + ", ".join(JOBS))
        parser.add_argument("--source", help="fetch_un_list only: a URL or file path instead of the usual address")

    def handle(self, *args, jobs, source=None, **opts):
        names = [n for j in jobs for n in (NIGHTLY if j == "nightly" else [j])]
        unknown = [n for n in names if n not in JOBS]
        if unknown:
            raise CommandError(f"Unknown job(s): {', '.join(unknown)}. Choices: nightly, {', '.join(JOBS)}")
        failed = False
        for name in names:
            kwargs = {"source": source} if name == "fetch_un_list" and source else {}
            run = run_job(name, JOBS[name], **kwargs)
            self.stdout.write(f"{name}: {run.get_status_display()} {run.summary or ''} {run.error}".rstrip())
            failed |= run.status == JobRun.Status.FAILED
        if failed:
            raise CommandError("One or more jobs failed; see the Jobs page.")    # non-zero exit for the scheduler's alerting
