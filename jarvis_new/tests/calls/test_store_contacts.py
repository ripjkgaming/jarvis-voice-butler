import pytest

from calls import calllog, contacts


def test_add_get_normalised():
    c = contacts.add_contact("Mum", "9123 4567", relationship="mother", trust="family")
    assert c.number == "+6591234567" and c.trust == "family" and c.policy == {}
    assert contacts.get_by_number("+65 9123-4567").id == c.id
    assert contacts.get_by_number("garbage") is None
    assert contacts.get_by_number("81234567") is None


def test_duplicate_and_validation():
    contacts.add_contact("A", "91234567")
    with pytest.raises(ValueError):
        contacts.add_contact("B", "+65 91234567")
    with pytest.raises(ValueError):
        contacts.add_contact("", "81234567")
    with pytest.raises(ValueError):
        contacts.add_contact("C", "81234567", trust="vip")
    with pytest.raises(ValueError):
        contacts.add_contact("C", "81234567", policy={"answer": "maybe"})
    with pytest.raises(ValueError):
        contacts.add_contact("C", "81234567", policy={"bogus": 1})
    with pytest.raises(ValueError):
        contacts.add_contact("C", "12")


def test_find_substring_nocase_exact_first():
    contacts.add_contact("Anna Lee", "91111111")
    contacts.add_contact("Ann", "92222222")
    contacts.add_contact("Bob", "93333333")
    assert [c.name for c in contacts.find("ann")] == ["Ann", "Anna Lee"]
    assert contacts.find("zzz") == []
    assert contacts.find("") == []
    assert contacts.find("%") == []


def test_update_and_policy_roundtrip():
    c = contacts.add_contact("A", "91234567")
    pol = {
        "answer": "auto",
        "persona": "owner",
        "greeting": "Hi",
        "allow_topics": ["school"],
    }
    u = contacts.update_contact(
        c.id, trust="work", policy=pol, number="81234567", name=" New  Name "
    )
    assert (u.trust, u.policy, u.number, u.name) == (
        "work",
        pol,
        "+6581234567",
        "New Name",
    )
    with pytest.raises(KeyError):
        contacts.update_contact(99, name="x")
    with pytest.raises(ValueError):
        contacts.update_contact(c.id, bogus=1)
    with pytest.raises(ValueError):
        contacts.update_contact(c.id, trust="x")
    other = contacts.add_contact("B", "92345678")
    with pytest.raises(ValueError):
        contacts.update_contact(other.id, number="81234567")
    assert contacts.get_contact(other.id).number == "+6592345678"


def test_delete_and_list_sorted():
    a = contacts.add_contact("zed", "91234567")
    contacts.add_contact("Amy", "81234567")
    assert [c.name for c in contacts.list_contacts()] == ["Amy", "zed"]
    assert contacts.delete_contact(a.id) is True
    assert contacts.delete_contact(a.id) is False
    assert len(contacts.list_contacts()) == 1


def test_import_whatsapp():
    contacts.add_contact("Existing", "91234567", trust="family")
    rows = [
        {"name": "Dup", "number": "+65 9123 4567"},
        {"name": "New1", "number": "81234567"},
        {"name": "New1b", "number": "6581234567"},  # same number as New1
        {"name": "UK", "number": "+44 7700 900123"},
        {"name": "Bad", "number": "12"},
        {"name": "", "number": "92345678"},
        {"number": "92345679"},
        "junk",
    ]
    assert contacts.import_whatsapp(rows) == 2
    assert contacts.get_by_number("91234567").trust == "family"
    assert contacts.get_by_number("91234567").name == "Existing"
    assert contacts.get_by_number("81234567").trust == "unknown"
    assert contacts.import_whatsapp(rows) == 0
    assert contacts.import_whatsapp([]) == 0
    assert any(a["kind"] == "contacts_import_whatsapp" for a in calllog.list_audit(20))
