"""SMS delivery abstraction supporting WebOTP API, Kavenegar (Iran), and Twilio (Global)."""

from abc import ABC, abstractmethod
import logging
import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def build_webotp_message(
    code: str,
    domain: str | None = None,
    app_name: str | None = None,
) -> str:
    """Format an SMS body compliant with the W3C WebOTP API specification.

    The last line must contain `@<domain> #<otp_code>` for mobile browsers (Chrome / Safari)
    to display the native one-tap autofill dialog.
    """
    settings = get_settings()
    domain_to_use = domain or settings.app_domain
    app_to_use = app_name or settings.app_name
    return f"کد تایید ورود شما به {app_to_use}: {code}\n\n@{domain_to_use} #{code}"


class SmsSender(ABC):
    """Interface for SMS delivery providers."""

    @abstractmethod
    def send(self, phone_number: str, message: str, otp_code: str | None = None) -> None:
        """Send an SMS message to the given phone number."""


class ConsoleSmsSender(SmsSender):
    """Dev and test sender that outputs the WebOTP payload to logs."""

    def send(self, phone_number: str, message: str, otp_code: str | None = None) -> None:
        logger.warning(
            "[DEV SMS] To: %s | OTP: %s | Payload:\n%s",
            phone_number,
            otp_code or "N/A",
            message,
        )


class KavenegarSmsSender(SmsSender):
    """Kavenegar provider for Iranian mobile networks (+98 / 09xx)."""

    def __init__(
        self,
        api_key: str,
        sender: str = "10008663",
        template: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.sender = sender
        self.template = template
        self.timeout = timeout

    def send(self, phone_number: str, message: str, otp_code: str | None = None) -> None:
        clean_phone = phone_number.replace("+98", "0")
        if clean_phone.startswith("0098"):
            clean_phone = "0" + clean_phone[4:]

        # 1. Pattern Lookup (خدماتی سریع بدون مسدودی بلک‌لیست تبلیغاتی)
        if self.template and otp_code:
            url = f"https://api.kavenegar.com/v1/{self.api_key}/verify/lookup.json"
            params = {
                "receptor": clean_phone,
                "token": otp_code,
                "template": self.template,
            }
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(url, params=params)
                    resp.raise_for_status()
                    logger.info(f"[Kavenegar] Lookup OTP sent to {clean_phone} with template {self.template}")
            except Exception as exc:
                logger.error(f"[Kavenegar] Failed to send lookup OTP to {clean_phone}: {exc}")
                raise
        else:
            # 2. Standard SMS Send with full WebOTP text
            url = f"https://api.kavenegar.com/v1/{self.api_key}/sms/send.json"
            data = {
                "receptor": clean_phone,
                "message": message,
                "sender": self.sender,
            }
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, data=data)
                    resp.raise_for_status()
                    logger.info(f"[Kavenegar] WebOTP SMS sent to {clean_phone}")
            except Exception as exc:
                logger.error(f"[Kavenegar] Failed to send SMS to {clean_phone}: {exc}")
                raise


class TwilioSmsSender(SmsSender):
    """Twilio provider for international mobile numbers."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        timeout: float = 10.0,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.timeout = timeout

    def send(self, phone_number: str, message: str, otp_code: str | None = None) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = {
            "To": phone_number,
            "From": self.from_number,
            "Body": message,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, data=data, auth=(self.account_sid, self.auth_token))
                resp.raise_for_status()
                logger.info(f"[Twilio] International WebOTP SMS sent to {phone_number}")
        except Exception as exc:
            logger.error(f"[Twilio] Failed to send SMS via Twilio to {phone_number}: {exc}")
            raise


class CompositeSmsSender(SmsSender):
    """Smart router: dispatches Iranian numbers to Kavenegar and global numbers to Twilio."""

    def send(self, phone_number: str, message: str, otp_code: str | None = None) -> None:
        settings = get_settings()
        is_iran = phone_number.startswith("+98") or phone_number.startswith("09") or phone_number.startswith("0098")

        if is_iran:
            if settings.kavenegar_api_key:
                sender = KavenegarSmsSender(
                    api_key=settings.kavenegar_api_key,
                    sender=settings.kavenegar_sender,
                    template=settings.kavenegar_otp_template,
                )
                sender.send(phone_number, message, otp_code=otp_code)
                return
        else:
            if (
                settings.twilio_account_sid
                and settings.twilio_auth_token
                and settings.twilio_from_number
            ):
                sender = TwilioSmsSender(
                    account_sid=settings.twilio_account_sid,
                    auth_token=settings.twilio_auth_token,
                    from_number=settings.twilio_from_number,
                )
                sender.send(phone_number, message, otp_code=otp_code)
                return

        # Fallback to Console sender in dev or when API keys are not yet configured
        ConsoleSmsSender().send(phone_number, message, otp_code=otp_code)


def get_sms_sender() -> SmsSender:
    """Return the active SMS sender router."""
    return CompositeSmsSender()