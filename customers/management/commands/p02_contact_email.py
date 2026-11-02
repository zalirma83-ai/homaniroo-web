"""Fixed-scope P02 operation; defaults to read-only preview."""
import json
from django.core.management.base import BaseCommand, CommandError
from customers.p02_contact_email import (
    ApprovalError, apply_approved_email, inspect_target, verify_runtime_source,
)


class Command(BaseCommand):
    help = 'Preview or apply only the approved P02 contact 167 email completion.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--backup-manifest', help='Fresh verified external backup manifest; required for --apply.')

    def handle(self, *args, **options):
        try:
            verify_runtime_source()
            if options['apply']:
                if not options['backup_manifest']:
                    raise ApprovalError('--apply requires a fresh verified --backup-manifest.')
                result = apply_approved_email(options['backup_manifest'])
            else:
                if options['backup_manifest']:
                    raise ApprovalError('Do not pass a backup manifest to read-only preview.')
                result = inspect_target()
            self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
        except ApprovalError as error:
            raise CommandError('STOPPED: ' + str(error)) from error
        except Exception as error:
            raise CommandError('STOPPED: ' + type(error).__name__ +
                '. If --apply was used, result may be uncertain; inspect state before retry. No blind retry.') from error
