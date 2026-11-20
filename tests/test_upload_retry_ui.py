"""Regression coverage for upload-proxy 5xx recovery in the browser UI."""

from scripts import upload_server


def test_transient_502_retries_resume_from_confirmed_offset_and_reset_budget():
    page = upload_server.page()
    assert "MAX_CONSECUTIVE_FAILURES=40" in page
    assert "xhr.status===502||xhr.status===503||xhr.status===504" in page
    assert "off=to; fails=0; tries=0" in page
    assert "const delay=Math.min(500*Math.pow(2,Math.min(tries,5)),15000)" in page
    assert "الجزء السابق محفوظ" in page


def test_upload_page_build_is_bumped_for_cache_refresh():
    page = upload_server.page()
    assert upload_server.BUILD == "93"
    assert "93" in page
