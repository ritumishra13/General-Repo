from common.picklist_utils import add_role, has_role, remove_role, roles_from_string, roles_to_string, swap_role


def test_roles_from_string_basic():
    assert roles_from_string("Owner;Care Of") == ["Owner", "Care Of"]


def test_roles_from_string_blank():
    assert roles_from_string("") == []
    assert roles_from_string(None) == []


def test_roles_from_string_strips_whitespace_and_drops_empty_tokens():
    assert roles_from_string(" Owner ; ;Care Of ") == ["Owner", "Care Of"]


def test_roles_to_string_dedupes_preserving_order():
    assert roles_to_string(["Owner", "Owner", "Care Of"]) == "Owner;Care Of"


def test_has_role_exact_token_not_substring():
    # "Owner" must NOT match inside "Previous Owner" -- the substring trap.
    assert has_role("Previous Owner", "Owner") is False
    assert has_role("Owner", "Owner") is True
    assert has_role("Owner;Care Of", "Owner") is True


def test_add_role_idempotent():
    assert add_role("Owner", "Owner") == "Owner"
    assert add_role("Owner", "Care Of") == "Owner;Care Of"
    assert add_role("", "Owner") == "Owner"


def test_remove_role_preserves_others():
    assert remove_role("Owner;Care Of", "Owner") == "Care Of"
    assert remove_role("Owner", "Owner") == ""


def test_remove_role_does_not_remove_substring_match():
    # Removing "Owner" must not affect "Previous Owner" sitting alongside it.
    assert remove_role("Owner;Previous Owner", "Owner") == "Previous Owner"


def test_swap_role_basic():
    assert swap_role("Owner", "Owner", "Previous Owner") == "Previous Owner"


def test_swap_role_preserves_other_roles():
    assert swap_role("Owner;Care Of", "Owner", "Previous Owner") == "Care Of;Previous Owner"


def test_swap_role_no_duplicate_if_target_already_present():
    assert swap_role("Owner;Previous Owner", "Owner", "Previous Owner") == "Previous Owner"


def test_swap_role_no_op_when_role_not_present():
    assert swap_role("Care Of", "Owner", "Previous Owner") == "Care Of;Previous Owner"
