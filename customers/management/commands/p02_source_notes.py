import json
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from customers import source_notes


class Command(BaseCommand):
    help = "Read-only preview of approved P02 source notes. Apply requires an approved plan and a fresh verified backup."

    def add_arguments(self,parser):
        parser.add_argument("--report",required=True)
        parser.add_argument("--input",required=True)
        parser.add_argument("--apply",action="store_true")
        parser.add_argument("--backup-manifest")

    def handle(self,*args,**options):
        try:
            source_notes.verify_runtime_source()
            plan=source_notes.load_approved_plan(options["report"],options["input"])
            if options["apply"]:
                if not options["backup_manifest"]:
                    raise source_notes.ApprovalError("--apply requires a fresh --backup-manifest.")
                result=source_notes.apply_plan(plan,options["backup_manifest"])
                self.stdout.write(json.dumps(result,ensure_ascii=False,indent=2))
                return
            if options["backup_manifest"]:
                raise source_notes.ApprovalError("Do not pass a backup receipt without --apply.")
            result=source_notes.inspect_plan(plan)
            # Private preview is an external report, never a file in the repository.
            folder=Path.home()/"Downloads"
            if folder.resolve().is_relative_to(Path(settings.BASE_DIR).resolve()):
                raise source_notes.ApprovalError("Report destination must be outside the project.")
            folder.mkdir(exist_ok=True)
            path=folder/("homaniroo_P02_notes_preview_"+datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")+".json")
            result.update(transaction_rolled_back=True,contains_private_source_notes=True,database_changed=False)
            with path.open("x",encoding="utf-8") as stream:
                json.dump(result,stream,ensure_ascii=False,indent=2)
            self.stdout.write("READ_ONLY_OK: no import or database changes.")
            self.stdout.write("NOTES_TO_INSERT: "+str(result["notes_to_insert"]))
            self.stdout.write("REPORT: "+str(path))
            self.stdout.write("STOP HERE. Review the private preview before any write.")
        except Exception as error:
            detail=": "+str(error) if isinstance(error,source_notes.ApprovalError) else ""
            raise CommandError("STOPPED: "+type(error).__name__+detail) from None
