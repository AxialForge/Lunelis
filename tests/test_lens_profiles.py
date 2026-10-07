"""0.38: add your own lensfun profiles, checked; version 2 files refused with a reason."""
from lunelis.edit import lens

V1 = ('<lensdatabase version="1"><lens><maker>Test</maker><model>Test 50mm f/1.8</model><mount>Sony E</mount>'
      '<cropfactor>1</cropfactor><calibration><distortion model="ptlens" focal="50" a="0" b="-0.01" c="0"/>'
      '</calibration></lens></lensdatabase>')


def test_profiles_are_checked_added_and_found(tmp_path, monkeypatch):
    monkeypatch.setattr(lens, "profile_dir", lambda: tmp_path / "lens profiles")
    from lunelis.ui.lens_profiles import add_profile
    good = tmp_path / "mine.xml"
    good.write_text(V1, encoding="utf-8")
    v2 = tmp_path / "new.xml"
    v2.write_text(V1.replace('version="1"', 'version="2"'), encoding="utf-8")
    junk = tmp_path / "junk.xml"
    junk.write_text("<html/>", encoding="utf-8")
    assert "version 1" in add_profile(str(v2))[1]
    assert add_profile(str(junk))[1]
    names, why = add_profile(str(good))
    assert why is None and names == ["Test Test 50mm f/1.8"]
    assert "already added" in add_profile(str(good))[1]
    assert any(x.model == "Test 50mm f/1.8" for x in lens._db().lenses)
    lens.reload()
