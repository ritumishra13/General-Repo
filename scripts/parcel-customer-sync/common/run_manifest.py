import json


class RunManifest:
    def __init__(self, pipeline, target_org, dry_run, source_file):
        self.data = {
            "pipeline": pipeline,
            "target_org": target_org,
            "dry_run": dry_run,
            "source_file": str(source_file),
            "counts": {},
            "written_ids": [],
        }

    def set_count(self, key, value):
        self.data["counts"][key] = value

    def record_write(self, sobject, record_id, operation, source_row_key=None):
        self.data["written_ids"].append(
            {
                "sobject": sobject,
                "id": record_id,
                "operation": operation,
                "source_row_key": source_row_key,
            }
        )

    def write(self, path):
        with open(path, "w") as f:
            json.dump(self.data, f, indent=2)
