"""Pydantic schemas for the auth feature."""

from datetime import datetime

from pydantic import BaseModel, Field , ConfigDict

class RegisterRequest(BaseModel):
    """Payload to request a registration OTP."""

    phone: str = Field(min_length=10, max_length=20)
    website: str | None = None 


class RegisterVerifyRequest(BaseModel):
    """Payload to verify the OTP and create the account."""

    phone: str = Field(min_length=10, max_length=20)
    code: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)


class LoginRequest(BaseModel):
    """Payload for password login."""

    phone: str = Field(min_length=10, max_length=20)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """Payload carrying an opaque refresh token."""

    refresh_token: str


class ResetRequest(BaseModel):
    """Payload to request a password-reset OTP."""

    phone: str = Field(min_length=10, max_length=20)


class ResetConfirmRequest(BaseModel):
    """Payload to set a new password with a reset OTP."""

    phone: str = Field(min_length=10, max_length=20)
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    """Access/refresh token pair returned on successful auth."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class OtpResponse(BaseModel):
    """Result of an OTP request; dev_otp_code only in DEBUG mode."""

    message: str
    dev_otp_code: str | None = None


class MessageResponse(BaseModel):
    """Simple message envelope."""

    message: str
    
    
class UserOut(BaseModel):
    """Public profile of the authenticated user with permissions."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_number: str
    display_name: str | None
    is_verified: bool
    is_owner: bool
    is_superuser: bool = False
    actions: list[str]
    scope: list[dict]


class RoleCreate(BaseModel):
    """Payload to create a new role."""

    name: str = Field(min_length=1, max_length=100)
    actions: list[str]
    scope: list[dict]


class RoleOut(BaseModel):
    """Public representation of a role."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    actions: list[str]
    scope: list[dict]


class RoleUpdate(BaseModel):
    """Payload to update an existing role."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    actions: list[str] | None = None
    scope: list[dict] | None = None
    

class InviteRequest(BaseModel):
    """Payload to invite a new team member."""

    phone: str = Field(min_length=10, max_length=20)
    role_id: int


class InviteOut(BaseModel):
    """Public representation of a member invite."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_number: str
    status: str
    expires_at: datetime
    role: RoleOut


class ActivateRequest(BaseModel):
    """Payload to activate an invite with OTP + password."""

    phone: str = Field(min_length=10, max_length=20)
    code: str = Field(pattern=r"^\d{6}$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)
    
    
class MemberOut(BaseModel):
    """Public representation of a team member."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    phone_number: str
    display_name: str | None
    is_active: bool
    role: RoleOut | None


class MemberUpdate(BaseModel):
    """Payload to update a team member (change role or deactivate)."""

    role_id: int | None = None
    is_active: bool | None = None