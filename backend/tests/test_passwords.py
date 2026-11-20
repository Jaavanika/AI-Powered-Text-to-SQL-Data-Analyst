import pytest

from app.core import passwords


def test_a_password_verifies_against_its_own_hash():
    stored = passwords.hash_password("correct horse battery")
    assert passwords.verify_password("correct horse battery", stored)
    assert not passwords.verify_password("correct horse batterY", stored)
    assert not passwords.verify_password("", stored)


def test_the_hash_never_contains_the_password_and_is_salted():
    one, two = passwords.hash_password("same password!"), passwords.hash_password("same password!")
    assert "same password!" not in one and one != two            # a fresh salt each time
    assert passwords.verify_password("same password!", one) and passwords.verify_password("same password!", two)


def test_the_parameters_are_stored_with_the_hash_so_they_can_be_raised_later():
    algorithm, n, r, p, salt, digest = passwords.hash_password("x" * 12).split("$")
    assert algorithm == "scrypt" and int(n) >= 2 ** 14 and int(r) >= 8 and int(p) >= 1 and salt and digest


def test_unicode_passwords_work():
    stored = passwords.hash_password("pässwörd-日本語-🔑")
    assert passwords.verify_password("pässwörd-日本語-🔑", stored)
    assert not passwords.verify_password("passwörd-日本語-🔑", stored)


@pytest.mark.parametrize("stored", ["", "plain text", "scrypt$1$2", "scrypt$x$8$1$AAAA$BBBB", "md5$1$8$1$AAAA$BBBB",
                                    "scrypt$16384$8$1$!!!$!!!", "scrypt$0$8$1$AAAA$BBBB"])
def test_a_damaged_or_foreign_hash_is_simply_a_failed_login(stored):
    assert passwords.verify_password("anything", stored) is False


def test_absurd_parameters_in_a_stored_hash_are_not_run():
    """A tampered row must not be able to make the server spend gigabytes or minutes on one login."""
    assert passwords.verify_password("anything", "scrypt$1073741824$8$1$AAAAAAAAAAAAAAAAAAAAAA$BBBB") is False


def test_a_dummy_hash_exists_for_unknown_users_and_never_matches():
    assert passwords.verify_password("anything at all", passwords.DUMMY_HASH) is False
