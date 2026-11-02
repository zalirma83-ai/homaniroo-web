"""Guarded P02 source-note import; default read-only, audited atomic writes after backup."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction

from customers.models import BillingAccount, CustomerSourceNote, IndustrialUnit, TenantCustomer
from interactions.models import Interaction
from auditlog.models import AuditLog
from auditlog.services import record_audit_event
from tenants.models import Tenant
from customers.p02_contact_email import ApprovalError, _digest, _image, endpoint_fingerprint, sha_file

HEAD = "7ec5400b50c215a6daa03fc29278e5fe9d925efd"
OPERATION = "P02_SOURCE_NOTES_65_V1"
SOURCE_SHA256 = "3855ca94224f86ba6f541f656f96edc7e35936f5aa7468945658a364284abdec"
REPORT_SHA256 = '54e422c5adec276c1517a7c68fd0a175e675981c5aad8223891d759492d33822'
APPROVED_ROWS = (76, 120, 124, 127, 128, 129, 131, 132, 133, 134, 135, 136, 137, 138, 140, 141, 142, 146, 148, 149, 150, 152, 153, 154, 155, 158, 159, 163, 164, 168, 169, 170, 171, 172, 173, 174, 176, 177, 180, 181, 183, 185, 186, 187, 188, 191, 192, 193, 195, 197, 198, 202, 203, 206, 207, 214, 215, 216, 226, 228, 230, 231, 232, 234, 238)
TENANT_ID = 1
MANIFEST = "docs/P02_NOTES_MANIFEST.json"


def source_notes_for_customer(tenant, customer):
    if customer.tenant_id != tenant.pk:
        raise PermissionDenied("پرونده متعلق به شرکت فعال نیست.")
    return CustomerSourceNote.objects.for_tenant(tenant).filter(tenant_customer_id=customer.pk)


def normalize_note(value):
    return re.sub(r"\s+", " ", str(value or "").translate(str.maketrans("يک", "یک"))).strip()


def load_approved_plan(report_path, input_path):
    root = Path(settings.BASE_DIR).resolve()
    paths = [Path(report_path), Path(input_path)]
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(root):
            raise ApprovalError("Keep the reviewed report and original Excel outside the project.")
    if sha_file(paths[0]) != REPORT_SHA256 or sha_file(paths[1]) != SOURCE_SHA256:
        raise ApprovalError("Report or Excel differs from the approved source; do not bypass guards.")
    report = json.loads(paths[0].read_text(encoding="utf-8"))
    if not report.get("transaction_read_only_verified") or not report.get("transaction_rolled_back"):
        raise ApprovalError("Expected read-only evidence not found.")
    plan = []
    for row in report["rows"]:
        if row["excel_row"] not in APPROVED_ROWS:
            continue
        accounts = row["existing_bill_records_in_target"]
        if len(accounts) != 1 or accounts[0]["tenant_id"] != TENANT_ID:
            raise ApprovalError("Approved row must have one unambiguous bill link.")
        account = accounts[0]
        candidate = next(c for c in row["target_company_candidates"] if c["tenant_customer_id"] == account["tenant_customer_id"])
        if not candidate["match_evidence"]["bill"] or not candidate["match_evidence"]["name_or_alias"]:
            raise ApprovalError("Name/bill agreement missing in approved row.")
        body = row["source_cells"]["L"]
        if not isinstance(body,str) or not body.strip():
            raise ApprovalError("Expected nonempty original note text.")
        plan.append({"source_row":row["excel_row"],"body":body,
            "body_sha256":hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "customer_id":account["tenant_customer_id"],"billing_account_id":account["billing_account_id"],
            "bill_id":account["bill_id"],"industrial_unit_id":candidate["industrial_unit_id"],
            "official_name":candidate["official_name"],"customer_status":candidate["customer_status"]})
    if len(plan)!=65 or sorted(p["source_row"] for p in plan)!=list(APPROVED_ROWS):
        raise ApprovalError("Approved 65-row plan is incomplete or duplicated.")
    return sorted(plan,key=lambda p:p["source_row"])


def verify_runtime_source():
    root = Path(settings.BASE_DIR).resolve()
    manifest_path = root/MANIFEST
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ApprovalError("Unsafe or missing source manifest.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("reference_head")!=HEAD or manifest.get("operation")!=OPERATION:
        raise ApprovalError("Unexpected source manifest.")
    for rel,expected in {**manifest["protected"],**manifest["installed_hashes"]}.items():
        p=root/rel
        if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(root):
            raise ApprovalError("Unsafe or missing source: "+rel)
        if hashlib.sha256(p.read_bytes().replace(b"\r\n",b"\n")).hexdigest()!=expected:
            raise ApprovalError("Reviewed source changed: "+rel)
    def git(*args):
        r=subprocess.run(["git","-C",str(root),*args],capture_output=True)
        if r.returncode:
            raise ApprovalError("Git verification failed.")
        return r.stdout.decode("utf-8","strict")
    if git("rev-parse","HEAD").strip()!=HEAD:
        raise ApprovalError("HEAD changed; review before this one-time operation.")
    allowed={"?? '","?? templates.zip"}
    for rel in [*manifest["installed_hashes"],MANIFEST]:
        allowed.update({"?? "+rel," M "+rel,"M  "+rel,"A  "+rel})
    if any(x and x not in allowed for x in git("status","--porcelain=v1","-z","--untracked-files=all").split("\0")):
        raise ApprovalError("Unexpected Git changes; send git status --short.")
    if connection.vendor!="postgresql" or str(connection.settings_dict.get("NAME"))!="homaniroo_dev":
        raise ApprovalError("Only the existing PostgreSQL homaniroo_dev is accepted.")
    return root


def inspect_targets(plan):
    tenant=Tenant.objects.get(pk=TENANT_ID,status="ACTIVE",display_name="هما نیرو")
    preview=[]; snapshots=[]; existing_ids=[]; pending_rows=[]
    for p in plan:
        customer=TenantCustomer.objects.get(pk=p["customer_id"],tenant_id=TENANT_ID,industrial_unit_id=p["industrial_unit_id"])
        unit=IndustrialUnit.objects.get(pk=p["industrial_unit_id"])
        account=BillingAccount.objects.get(pk=p["billing_account_id"],tenant_id=TENANT_ID,
            tenant_customer_id=customer.pk,bill_id=p["bill_id"])
        if customer.customer_status!=p["customer_status"] or unit.official_name!=p["official_name"]:
            raise ApprovalError("Customer identity/status changed; no overwrite permitted.")
        if BillingAccount.objects.filter(tenant_id=TENANT_ID,bill_id=p["bill_id"]).exclude(pk=account.pk).exists():
            raise ApprovalError("Bill identity is now ambiguous.")
        interactions=list(Interaction.objects.filter(tenant_id=TENANT_ID,tenant_customer_id=customer.pk).order_by("interaction_id"))
        found=list(CustomerSourceNote.objects.filter(tenant_id=TENANT_ID,source_sha256=SOURCE_SHA256,
            source_sheet="Sheet1",source_row=p["source_row"],source_column="L"))
        events=list(AuditLog.objects.filter(tenant_id=TENANT_ID,entity_type="CUSTOMER_SOURCE_NOTE",
            metadata__operation=OPERATION,metadata__source_excel_row=p["source_row"]))
        if found:
            if len(found)!=1:
                raise ApprovalError("Duplicate source-note origin; manual review required.")
            note=found[0]
            expected={"tenant_customer_id":customer.pk,"billing_account_id":account.pk,
                "body":p["body"],"body_sha256":p["body_sha256"],"source_filename":"آذربایجان (1).xlsx"}
            if any(getattr(note,k)!=v for k,v in expected.items()):
                raise ApprovalError("Existing source note differs; no overwrite allowed.")
            if len(events)!=1 or events[0].entity_id!=str(note.pk) or not events[0].success:
                raise ApprovalError("Source note already exists without consistent audit evidence.")
            expected_meta={"source_sha256":SOURCE_SHA256,"body_sha256":p["body_sha256"],
                "customer_id":customer.pk,"billing_account_id":account.pk,"source_sheet":"Sheet1","source_column":"L"}
            if any(events[0].metadata.get(k)!=v for k,v in expected_meta.items()):
                raise ApprovalError("Audit evidence differs; manual review required.")
            existing_ids.append(note.pk)
        else:
            if events:
                raise ApprovalError("Audit exists without its source note; manual review required.")
            pending_rows.append(p["source_row"])
        if any(normalize_note(p["body"]) in (normalize_note(i.notes),normalize_note(i.interaction_status)) for i in interactions):
            raise ApprovalError("Note now exists in interaction history; avoid duplication and re-review.")
        preview.append({"excel_row":p["source_row"],"customer_id":customer.pk,"customer_name":unit.official_name,
            "customer_status":customer.customer_status,"billing_account_id":account.pk,"body":p["body"]})
        snapshots.append({"customer":_image(customer),"unit":_image(unit),"account":_image(account),
            "interactions":[_image(i) for i in interactions]})
    if existing_ids and pending_rows:
        raise ApprovalError("Partial prior import detected; no blind retry allowed.")
    return {"state":"READY" if pending_rows else "ALREADY_APPLIED","operation":OPERATION,"database":"homaniroo_dev","tenant_id":TENANT_ID,
        "snapshot_sha256":_digest({"tenant":_image(tenant),"records":snapshots}),
        "endpoint_fingerprint":endpoint_fingerprint(),"notes_to_insert":len(pending_rows),"existing_notes":len(existing_ids),
        "new_interactions":0,"new_customers":0,"status_changes":0,"rows":preview}


def inspect_plan(plan):
    if connection.in_atomic_block:
        raise ApprovalError("Preflight must own its read-only transaction.")
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SET LOCAL statement_timeout = '30s'")
            cursor.execute("SELECT current_setting('transaction_read_only')")
            if cursor.fetchone()[0]!="on":
                raise ApprovalError("Read-only guard failed.")
        result=inspect_targets(plan)
        transaction.set_rollback(True)
    return result


APPROVED_PREVIEW_SHA256 = "0b8e321e0ec02bf8b7cd41ad1dabbe72c8cbb3d7ba307dd211d8040ede831b6e"
APPROVED_SNAPSHOT = "df82b104ad636f764dce49d5bc724c0e060893c74abdb059a3046cd375ab9bcc"
EXPECTED_ENDPOINT = "66b71389a490c01cc55627b0f125bec0b018bfc49b2f397012939ec4044842b3"


def verify_note_schema():
    """Fail closed on unexpected migrations, identity, uniqueness or custom triggers."""
    from django.db.migrations.executor import MigrationExecutor
    executor=MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        raise ApprovalError("Pending migrations; do not import or run them automatically.")
    table=CustomerSourceNote._meta.db_table
    def query(sql,params):
        with connection.cursor() as cursor:
            cursor.execute(sql,params)
            return [dict(zip([c[0] for c in cursor.description],row)) for row in cursor.fetchall()]
    columns=query("""SELECT attname,format_type(atttypid,atttypmod) AS data_type,attnotnull,attidentity
        FROM pg_attribute WHERE attrelid=to_regclass(%s) AND attnum>0 AND NOT attisdropped ORDER BY attnum""",[table])
    names={c["attname"]:c for c in columns}
    expected_types={"id":"bigint","tenant_id":"bigint","tenant_customer_id":"bigint","billing_account_id":"bigint",
        "body":"text","body_sha256":"character varying(64)","source_filename":"character varying(255)",
        "source_sha256":"character varying(64)","source_sheet":"character varying(100)","source_row":"integer",
        "source_column":"character varying(3)","imported_at":"timestamp with time zone"}
    if set(names)!=set(expected_types) or any(names[k]["data_type"]!=v or not names[k]["attnotnull"] for k,v in expected_types.items()):
        raise ApprovalError("Source-note schema differs from reviewed model.")
    if names["id"]["attidentity"] not in ("a","d"):
        raise ApprovalError("Source-note primary key must be database-generated identity.")
    constraints=query("""SELECT conname,contype,convalidated,pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid=to_regclass(%s) ORDER BY conname""",[table])
    required={"source_note_origin_uniq","source_note_scope_positive","source_note_body_nonempty"}
    if not required.issubset({c["conname"] for c in constraints}) or not all(c["convalidated"] for c in constraints):
        raise ApprovalError("Required note constraints missing or not validated.")
    if not any(c["contype"]=="u" and c["definition"]=="UNIQUE (tenant_id, source_sha256, source_sheet, source_row, source_column)" for c in constraints):
        raise ApprovalError("Source-note uniqueness differs.")
    if not any(c["contype"]=="p" and c["definition"]=="PRIMARY KEY (id)" for c in constraints):
        raise ApprovalError("Source-note primary key differs.")
    triggers=query("""SELECT tgname FROM pg_trigger WHERE tgrelid IN (to_regclass(%s),to_regclass(%s))
        AND NOT tgisinternal ORDER BY tgname""",[table,AuditLog._meta.db_table])
    if triggers:
        raise ApprovalError("Non-internal note/audit triggers need review before import.")
    return _digest({"table":table,"columns":columns,"constraints":constraints})


def validate_import_backup(path,root,snapshot,schema_fingerprint):
    from datetime import datetime,timezone
    supplied=Path(path)
    if supplied.is_symlink() or not supplied.is_file() or supplied.resolve().is_relative_to(root):
        raise ApprovalError("Fresh external backup manifest required.")
    supplied=supplied.resolve()
    receipt=json.loads(supplied.read_text(encoding="utf-8"))
    expected={"status":"VERIFIED","operation":OPERATION,"source_head":HEAD,"source_sha256":SOURCE_SHA256,
        "database":"homaniroo_dev","endpoint_fingerprint":EXPECTED_ENDPOINT,
        "before_snapshot_sha256":snapshot,"after_snapshot_sha256":snapshot,"schema_fingerprint":schema_fingerprint,
        "archive_list_verified":True,"migration_executed":False,"notes_imported":False}
    if any(receipt.get(k)!=v for k,v in expected.items()):
        raise ApprovalError("Backup receipt differs from this approved note-import operation.")
    stamp=datetime.fromisoformat(receipt["created_at_utc"])
    if stamp.tzinfo is None or not -60<=(datetime.now(timezone.utc)-stamp).total_seconds()<=900:
        raise ApprovalError("Backup is stale; create a fresh verified backup.")
    dump=Path(receipt["backup_path"])
    if dump.is_symlink() or not dump.is_file():
        raise ApprovalError("Backup archive missing or unsafe.")
    dump=dump.resolve()
    if dump.is_relative_to(root) or dump.parent!=supplied.parent or dump.stat().st_size!=receipt.get("size_bytes") or dump.stat().st_size<=5:
        raise ApprovalError("Unexpected backup location or size.")
    with dump.open("rb") as stream:
        if stream.read(5)!=b"PGDMP":
            raise ApprovalError("Backup is not a PostgreSQL custom archive.")
    if sha_file(dump)!=receipt.get("sha256"):
        raise ApprovalError("Backup checksum differs.")
    restore=Path(r"D:\Program Files\PostgreSQL\17\bin\pg_restore.exe")
    if not restore.is_file():
        raise ApprovalError("Confirmed PostgreSQL 17 pg_restore tool missing.")
    listed=subprocess.run([str(restore),"--list",str(dump)],capture_output=True,timeout=60)
    if listed.returncode or not any(line and not line.startswith(b";") for line in listed.stdout.splitlines()):
        raise ApprovalError("Backup list verification failed; no restore attempted.")
    return receipt


@transaction.atomic
def _apply_locked(plan,expected_snapshot):
    if connection.vendor=="postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '60s'")
            cursor.execute("SELECT pg_advisory_xact_lock(%s,%s)",[20402,TENANT_ID])
    # Parent-row locks serialize this import with company/customer edits.
    tenant=Tenant.objects.select_for_update().get(pk=TENANT_ID,status="ACTIVE",display_name="هما نیرو")
    for model,ids in ((TenantCustomer,{p["customer_id"] for p in plan}),
                      (IndustrialUnit,{p["industrial_unit_id"] for p in plan}),
                      (BillingAccount,{p["billing_account_id"] for p in plan})):
        list(model.objects.select_for_update().filter(pk__in=sorted(ids)).order_by(model._meta.pk.name))
    state=inspect_targets(plan)
    if state["state"]=="ALREADY_APPLIED":
        return {"state":"ALREADY_APPLIED","notes_created":0,"audit_created":0,"existing_notes":state["existing_notes"]}
    if state["snapshot_sha256"]!=expected_snapshot:
        raise ApprovalError("Target changed after approved preview/backup; no blind retry.")
    for p in plan:
        note=CustomerSourceNote.objects.create(tenant_id=TENANT_ID,tenant_customer_id=p["customer_id"],
            billing_account_id=p["billing_account_id"],body=p["body"],body_sha256=p["body_sha256"],
            source_filename="آذربایجان (1).xlsx",source_sha256=SOURCE_SHA256,source_sheet="Sheet1",
            source_row=p["source_row"],source_column="L")
        event=record_audit_event(action="CREATE_OR_UPDATE",tenant=tenant,entity_type="CUSTOMER_SOURCE_NOTE",entity_id=note.pk,
            description="ثبت یادداشت فایل منبع، بدون ساختن تماس یا تاریخ وقوع فرضی.",severity="IMPORTANT",
            metadata={"operation":OPERATION,"customer_id":p["customer_id"],"billing_account_id":p["billing_account_id"],
                "source_sha256":SOURCE_SHA256,"source_sheet":"Sheet1","source_excel_row":p["source_row"],
                "source_column":"L","body_sha256":p["body_sha256"],"approved_preview_sha256":APPROVED_PREVIEW_SHA256,
                "executed_via":"management_command","actor_is_not_attributed_to_a_django_user":True})
        if event is None:
            raise ApprovalError("Audit write failed; all note writes rolled back.")
    after=inspect_targets(plan)
    if after["state"]!="ALREADY_APPLIED" or after["existing_notes"]!=len(plan) or after["snapshot_sha256"]!=expected_snapshot:
        raise ApprovalError("Post-write verification failed; transaction rolled back.")
    return {"state":"APPLIED","notes_created":len(plan),"audit_created":len(plan),
        "new_interactions":0,"new_customers":0,"status_changes":0}


def apply_plan(plan,backup_manifest):
    root=verify_runtime_source()
    if len(plan)!=65 or tuple(p["source_row"] for p in plan)!=APPROVED_ROWS:
        raise ApprovalError("Only the approved 65-row plan is accepted.")
    current=inspect_plan(plan)
    if current["endpoint_fingerprint"]!=EXPECTED_ENDPOINT:
        raise ApprovalError("Database endpoint differs.")
    if current["state"]=="ALREADY_APPLIED":
        return {"state":"ALREADY_APPLIED","notes_created":0,"audit_created":0,"existing_notes":current["existing_notes"]}
    if current["snapshot_sha256"]!=APPROVED_SNAPSHOT:
        raise ApprovalError("Data differs from approved preview; stop and re-review.")
    with transaction.atomic():
        with connection.cursor() as cursor:cursor.execute("SET TRANSACTION READ ONLY")
        schema_fingerprint=verify_note_schema()
        transaction.set_rollback(True)
    validate_import_backup(backup_manifest,root,current["snapshot_sha256"],schema_fingerprint)
    return _apply_locked(plan,current["snapshot_sha256"])
