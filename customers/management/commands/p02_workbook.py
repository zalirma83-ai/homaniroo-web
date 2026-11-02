import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand,CommandError
from customers import p02_workbook as service
from customers import p02_workbook_import as importer


class Command(BaseCommand):
    help='Read-only P02 review by default; exact approved batch apply requires a fresh verified backup.'

    def add_arguments(self,parser):
        parser.add_argument('--report',default=str(Path.home()/'Downloads'/'homaniroo_P02_remaining_readonly_20261101_232038_319299.json'))
        parser.add_argument('--input',default=str(Path.home()/'Downloads'/'آذربایجان (1).xlsx'))
        parser.add_argument('--approved-preview',default=str(Path.home()/'Downloads'/'homaniroo_P02_workbook_preview_20261102_003115_198686.json'))
        parser.add_argument('--apply',action='store_true',help='Only the reviewed batch, after separate approval and verified fresh backup.')
        parser.add_argument('--backup-manifest')

    def handle(self,*args,**options):
        try:
            service.verify_runtime_source()
            source=service.load_source(options['report'],options['input'])
            plan=importer.load_approval(options['approved_preview'])
            if options['apply']:
                result=importer.apply_import(source,plan,options['backup_manifest'])
                self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2))
                return
            if options['backup_manifest']:raise service.ApprovalError('--backup-manifest only with separately approved --apply.')
            result=importer.inspect_import(source,plan)
            result.update(summary=plan['summary'],database_changed=False,transaction_rolled_back=True)
            folder=Path.home()/'Downloads'
            if folder.resolve().is_relative_to(Path(settings.BASE_DIR).resolve()):raise service.ApprovalError('Keep report outside project.')
            folder.mkdir(exist_ok=True)
            path=folder/('homaniroo_P02_workbook_verification_'+datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')+'.json')
            with path.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
            self.stdout.write('READ_ONLY_OK: no source or database changes.')
            self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2))
            self.stdout.write('REPORT: '+str(path))
            self.stdout.write('STOP HERE. No import authorized by read-only verification.')
        except Exception as error:
            raise CommandError('STOPPED: '+type(error).__name__+(': '+str(error) if isinstance(error,service.ApprovalError) else '')) from None
