"""Helpers for Salesforce multi-select picklist strings.

Salesforce represents a multi-select picklist value as a ';'-delimited string,
e.g. "Owner;Care Of". All comparisons here are exact-token after splitting on
';' -- never substring matching -- because "Owner" is a substring of
"Previous Owner" and a naive `in` check on the raw string would collide the two.
"""


def roles_from_string(value):
    if not value:
        return []
    return [token.strip() for token in value.split(";") if token.strip()]


def roles_to_string(roles):
    seen = []
    for role in roles:
        if role not in seen:
            seen.append(role)
    return ";".join(seen)


def has_role(value, role):
    return role in roles_from_string(value)


def add_role(value, role):
    roles = roles_from_string(value)
    if role not in roles:
        roles.append(role)
    return roles_to_string(roles)


def remove_role(value, role):
    roles = [r for r in roles_from_string(value) if r != role]
    return roles_to_string(roles)


def swap_role(value, remove, add):
    roles = roles_from_string(value)
    roles = [r for r in roles if r != remove]
    if add not in roles:
        roles.append(add)
    return roles_to_string(roles)
