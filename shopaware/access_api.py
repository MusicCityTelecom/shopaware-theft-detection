import sqlite3
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict
from shopaware.access import AccessControl


class UserInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=128)
    role: Literal['admin', 'user'] = 'user'
    enabled: bool = True
    camera_ids: list[str] = Field(default_factory=list, max_length=1000)
    group_ids: list[str] = Field(default_factory=list, max_length=1000)


class NewUserInput(UserInput):
    password: str = Field(min_length=12, max_length=1024)


class PasswordInput(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class GroupInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class CameraGroupInput(BaseModel):
    group_id: str | None = Field(default=None, min_length=1, max_length=128)


def access_router(get_database, move_camera):
    router = APIRouter(tags=['users and customers'])

    def invoke(method, *args):
        try:
            return getattr(AccessControl(get_database()), method)(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Username or group name already exists, or an assignment is invalid') from None

    @router.get('/groups')
    def groups(request: Request):
        return invoke('groups', request.state.session)

    @router.post('/groups', status_code=201)
    def create_group(payload: GroupInput):
        return invoke('save_group', payload.name)

    @router.put('/groups/{group_id}')
    def update_group(group_id: str, payload: GroupInput):
        return invoke('save_group', payload.name, group_id)

    @router.delete('/groups/{group_id}')
    def delete_group(group_id: str):
        invoke('delete_group', group_id)
        return {'deleted': True}

    @router.put('/cameras/{camera_id}/group')
    def camera_group(camera_id: str, payload: CameraGroupInput):
        try:
            move_camera(camera_id, payload.group_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        return {'group_id': payload.group_id}

    @router.get('/users')
    def users():
        return invoke('users')

    @router.post('/users', status_code=201)
    def create_user(payload: NewUserInput, request: Request):
        return invoke('save_user', request.state.session['id'], payload)

    @router.put('/users/{user_id}')
    def update_user(user_id: int, payload: UserInput, request: Request):
        return invoke('save_user', request.state.session['id'], payload, user_id)

    @router.post('/users/{user_id}/password')
    def reset_password(user_id: int, payload: PasswordInput, request: Request):
        invoke('reset_password', request.state.session['id'], user_id, payload.password)
        return {'message': 'Password reset; all user sessions revoked'}

    @router.delete('/users/{user_id}')
    def delete_user(user_id: int, request: Request):
        invoke('delete_user', request.state.session['id'], user_id)
        return {'deleted': True}

    return router
