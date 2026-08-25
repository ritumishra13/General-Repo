import yaml

from common.sf_client import describe


def load(config_path):
    with open(config_path) as f:
        return yaml.safe_load(f)


def validate_against_org(mapping, target_org):
    """Re-validate every configured field name against a live describe.
    Fails fast with a clear message if a configured field no longer exists,
    rather than surfacing a confusing mid-run SOQL error.
    """
    checks = [
        (mapping["parcel"]["sobject"], [mapping["parcel"]["name_field"], mapping["parcel"]["census_tract_field"]]),
        (mapping["customer"]["sobject"], [mapping["customer"]["name_field"], mapping["customer"]["account_lookup_field"]]),
        (
            mapping["parcel_customer"]["sobject"],
            [
                mapping["parcel_customer"]["parcel_lookup_field"],
                mapping["parcel_customer"]["customer_lookup_field"],
                mapping["parcel_customer"]["account_lookup_field"],
                mapping["parcel_customer"]["association_role_field"],
            ],
        ),
    ]

    errors = []
    for sobject, fields in checks:
        described = describe(sobject, target_org)
        real_field_names = {f["name"] for f in described["fields"]}
        for field in fields:
            if field not in real_field_names:
                errors.append(f"{sobject}.{field} not found in org (target-org={target_org})")

    if errors:
        raise ValueError(
            "field_mapping.yaml is out of date with the org:\n  " + "\n  ".join(errors)
        )
