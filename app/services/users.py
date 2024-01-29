from datetime import datetime
from gettext import gettext as _
from json import dumps, loads
from urllib.parse import urlencode
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator
from sqlalchemy.ext.asyncio.session import AsyncSession

from app.configs import feature_flags, settings
from app.repositories.models import (
    User as UserModel,
    UserAlreadyExists,
    UserDoesNotExist,
)
from app.services.emails import ConfirmationBody, send_confirmation
from app.services.hashers import hasher
from app.services.security import AccessTokenData, generate_jwt_signature


class UserBase(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=64,
    )
    surname: str = Field(
        min_length=1,
        max_length=64,
    )
    middle_name: str | None = Field(
        default=None,
        alias="middleName",
        min_length=1,
        max_length=64,
    )
    email: EmailStr
    username: str = Field(
        min_length=5,
        max_length=64,
    )
    password: str = Field(
        min_length=8,
        max_length=128,
    )
    is_subscribed: bool = Field(
        default=True,
        alias="isSubscribed",
    )

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )


class UserAdd(UserBase):
    @field_validator("password", mode="before")
    @classmethod
    def hash_password(cls, value: str) -> str:
        return hasher.encode(value)


class User(UserBase):
    id: int
    is_confirmed: bool = Field(alias="isConfirmed")
    created_at: datetime = Field(alias="createdAt")


def _get_signature(uuid: UUID):
    return generate_jwt_signature(
        loads(
            dumps(
                {
                    "uuid": uuid,
                },
                default=str,
            )
        )
    )


def get_confirmation_body(user: UserModel, /) -> ConfirmationBody:
    url = HttpUrl.build(
        scheme="https",
        host=settings.trusted_host,
        path="api/users/activate",
        query=urlencode(
            {
                "sig": _get_signature(user.uuid),
            }
        ),
    )
    return ConfirmationBody(
        url=url,
        user={
            "name": user.name,
            "surname": user.surname,
        },
    )


async def process_add_user(body: UserAdd, session: AsyncSession, /) -> User:
    try:
        user: UserModel = await UserModel.add(session, body)
    except UserAlreadyExists:
        raise HTTPException(
            detail="User with username or email already exists",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    if feature_flags.activate_users:
        await send_confirmation(
            [user.email],
            _("FuturamaAPI - Account Activation"),
            get_confirmation_body(user),
        )
    return User.model_validate(user)


async def process_get_me(token: AccessTokenData, session: AsyncSession, /) -> User:
    try:
        user: UserModel = await UserModel.get(session, token.uuid, field=UserModel.uuid)
    except UserDoesNotExist:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    return User.model_validate(user)
