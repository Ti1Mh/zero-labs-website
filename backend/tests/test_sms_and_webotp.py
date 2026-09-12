"""Unit and integration tests for WebOTP message formatting and multi-provider SMS delivery."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from app.auth.sms import (
    CompositeSmsSender,
    ConsoleSmsSender,
    KavenegarSmsSender,
    TwilioSmsSender,
    build_webotp_message,
)
from app.core.config import Settings


def test_build_webotp_message_default_formatting():
    """Verify that build_webotp_message outputs W3C standard @domain #code on the last line."""
    code = "782194"
    msg = build_webotp_message(code=code, domain="mezonflow.ir", app_name="مزون فلو")
    
    assert f": {code}" in msg
    lines = msg.strip().split("\n")
    # The last non-empty line must strictly follow the W3C WebOTP format: @<domain> #<otp>
    assert lines[-1] == f"@mezonflow.ir #{code}"


def test_build_webotp_message_custom_values():
    """Verify custom domain and app name override."""
    code = "123456"
    msg = build_webotp_message(code=code, domain="custom.dev", app_name="ZeroLabs")
    assert "ZeroLabs" in msg
    assert msg.endswith(f"@custom.dev #{code}")


def test_composite_sms_fallback_to_console(caplog):
    """When no API keys are set, router logs dev SMS for both Iranian and foreign numbers."""
    dummy_settings = Settings(
        app_domain="test.mezonflow.ir",
        kavenegar_api_key=None,
        twilio_account_sid=None,
    )
    with patch("app.auth.sms.get_settings", return_value=dummy_settings):
        router = CompositeSmsSender()
        
        # Test Iran phone
        router.send("+989121112233", "Hello Iran", otp_code="112233")
        # Test International phone
        router.send("+14155552671", "Hello Global", otp_code="445566")

    # Assert console warnings logged
    assert "09121112233" in caplog.text or "+989121112233" in caplog.text
    assert "112233" in caplog.text
    assert "+14155552671" in caplog.text
    assert "445566" in caplog.text


def test_kavenegar_pattern_lookup_dispatch():
    """Verify fast pattern verify/lookup.json is called when template & otp_code are present."""
    sender = KavenegarSmsSender(
        api_key="kaveh_test_key",
        template="verify_otp",
    )
    
    req = httpx.Request("GET", "https://api.kavenegar.com/v1/kaveh_test_key/verify/lookup.json")
    mock_resp = httpx.Response(200, json={"return": {"status": 200, "message": "OK"}}, request=req)
    with patch("httpx.Client.get", return_value=mock_resp) as mock_get:
        sender.send("+989123456789", "Full text ignored in lookup", otp_code="998877")

        mock_get.assert_called_once()
        call_url = mock_get.call_args[0][0]
        call_params = mock_get.call_args[1]["params"]

        assert "https://api.kavenegar.com/v1/kaveh_test_key/verify/lookup.json" == call_url
        assert call_params["receptor"] == "09123456789"
        assert call_params["token"] == "998877"
        assert call_params["template"] == "verify_otp"


def test_kavenegar_standard_send_dispatch():
    """Verify sms/send.json is called when no template is configured."""
    sender = KavenegarSmsSender(
        api_key="kaveh_test_key",
        sender="10008663",
        template=None,
    )
    
    req = httpx.Request("POST", "https://api.kavenegar.com/v1/kaveh_test_key/sms/send.json")
    mock_resp = httpx.Response(200, json={"return": {"status": 200, "message": "OK"}}, request=req)
    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        full_message = "کد تایید: 1234\n\n@mezonflow.ir #1234"
        sender.send("09123456789", full_message, otp_code="1234")

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        call_data = mock_post.call_args[1]["data"]

        assert "https://api.kavenegar.com/v1/kaveh_test_key/sms/send.json" == call_url
        assert call_data["receptor"] == "09123456789"
        assert call_data["message"] == full_message
        assert call_data["sender"] == "10008663"


def test_twilio_send_dispatch():
    """Verify Twilio international SMS dispatch and basic auth."""
    sender = TwilioSmsSender(
        account_sid="AC_MOCK_SID",
        auth_token="AUTH_TOKEN_SECRET",
        from_number="+15005550006",
    )

    req = httpx.Request("POST", "https://api.twilio.com/2010-04-01/Accounts/AC_MOCK_SID/Messages.json")
    mock_resp = httpx.Response(201, json={"sid": "SM_MOCK_MESSAGE_SID", "status": "queued"}, request=req)
    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        message = "Your verification code: 54321\n\n@mezonflow.ir #54321"
        sender.send("+447700900077", message, otp_code="54321")

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        call_data = mock_post.call_args[1]["data"]
        call_auth = mock_post.call_args[1]["auth"]

        assert "https://api.twilio.com/2010-04-01/Accounts/AC_MOCK_SID/Messages.json" == call_url
        assert call_data["To"] == "+447700900077"
        assert call_data["From"] == "+15005550006"
        assert call_data["Body"] == message
        assert call_auth == ("AC_MOCK_SID", "AUTH_TOKEN_SECRET")


def test_composite_routing_iran_vs_global():
    """Composite router accurately selects Kavenegar for Iran and Twilio for foreign numbers."""
    settings = Settings(
        app_domain="mezonflow.ir",
        kavenegar_api_key="kavenegar_active_key",
        kavenegar_sender="10008663",
        twilio_account_sid="twilio_sid",
        twilio_auth_token="twilio_token",
        twilio_from_number="+1000000000",
    )

    with patch("app.auth.sms.get_settings", return_value=settings):
        router = CompositeSmsSender()

        with patch.object(KavenegarSmsSender, "send") as mock_kav, \
             patch.object(TwilioSmsSender, "send") as mock_twi:

            # 1. 0912 number -> Kavenegar
            router.send("09121113344", "Msg Iran", otp_code="1111")
            mock_kav.assert_called_once_with("09121113344", "Msg Iran", otp_code="1111")
            mock_twi.assert_not_called()

            mock_kav.reset_mock()
            mock_twi.reset_mock()

            # 2. +98935 number -> Kavenegar
            router.send("+989351113344", "Msg Iran +98", otp_code="2222")
            mock_kav.assert_called_once_with("+989351113344", "Msg Iran +98", otp_code="2222")
            mock_twi.assert_not_called()

            mock_kav.reset_mock()
            mock_twi.reset_mock()

            # 3. +49 German number -> Twilio
            router.send("+491512345678", "Msg Germany", otp_code="3333")
            mock_twi.assert_called_once_with("+491512345678", "Msg Germany", otp_code="3333")
            mock_kav.assert_not_called()
