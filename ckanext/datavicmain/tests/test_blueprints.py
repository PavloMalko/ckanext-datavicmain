from __future__ import annotations

from typing import Any

import pytest

import ckan.plugins.toolkit as tk
from ckan.tests.helpers import call_action
from ckan.plugins.toolkit import url_for


@pytest.mark.usefixtures("clean_db", "with_plugins", "with_request_context")
class TestDatavicUserEndpoints:
    def test_user_approve(self, app, user, sysadmin):
        url = url_for("datavicuser.approve", id=user["id"])
        env = {"Authorization": sysadmin["token"]}

        response = app.get(url=url, extra_environ=env, status=200)

        assert "User approved" in response

    def test_user_approve_not_authorized(self, app, user):
        url = url_for("datavicuser.approve", id=user["id"])
        env = {"Authorization": user["token"]}

        response = app.get(url=url, extra_environ=env, status=403)

        assert "Unauthorized to activate user" in response

    def test_user_deny(self, app, sysadmin, user):
        url = url_for("datavicuser.deny", id=user["id"])
        env = {"Authorization": sysadmin["token"]}

        response = app.get(url=url, extra_environ=env, status=200)

        assert "User Denied" in response

    def test_user_deny_not_authorized(self, app, user):
        url = url_for("datavicuser.deny", id=user["id"])
        env = {"Authorization": user["token"]}

        response = app.get(url=url, extra_environ=env, status=403)

        assert "Unauthorized to reject user" in response


@pytest.mark.usefixtures("clean_db", "with_plugins", "with_request_context")
class TestDatavicUserUpdate:
    def test_regular_user_update_wrong_old(self, app, user):
        response = app.post(
            url=url_for("datavicuser.edit"),
            extra_environ={"Authorization": user["token"]},
            status=200,
            data={
                "save": "",
                "email": user["email"],
                "name": user["name"],
                "old_password": "wrong-old-pass",
                "password1": "123",
                "password2": "123",
            },
        )

        assert "Old Password: incorrect password" in response

    def test_regular_user_update_proper_old(self, app, user):
        response = app.post(
            url=url_for("datavicuser.edit"),
            extra_environ={"Authorization": user["token"]},
            status=200,
            data={
                "save": "",
                "email": user["email"],
                "name": user["name"],
                "old_password": "correct123",
                "password1": "new-pass-123",
                "password2": "new-pass-123",
            },
        )

        assert "Profile updated" in response

    def test_sysadmin_do_not_need_old_for_other_users(
        self, app, user, sysadmin
    ):
        response = app.post(
            url=url_for("datavicuser.edit", id=user["name"]),
            extra_environ={"Authorization": sysadmin["token"]},
            data={
                "save": "",
                "email": user["email"],
                "name": user["name"],
                "password1": "new-pass-123",
                "password2": "new-pass-123",
            },
        )
        assert "Profile updated" in response

    def test_sysadmin_need_old_for_themselves(self, app, sysadmin):
        response = app.post(
            url=url_for("datavicuser.edit", id=sysadmin["name"]),
            extra_environ={"Authorization": sysadmin["token"]},
            data={
                "save": "",
                "email": sysadmin["email"],
                "name": sysadmin["name"],
                "old_password": "wrong-old-pass",
                "password1": "new-pass-123",
                "password2": "new-pass-123",
            },
        )
        assert "Old Password: incorrect password" in response

    def test_sysadmin_update_with_old_pass(self, app, sysadmin):
        response = app.post(
            url=url_for("datavicuser.edit", id=sysadmin["name"]),
            extra_environ={"Authorization": sysadmin["token"]},
            data={
                "save": "",
                "email": sysadmin["email"],
                "name": sysadmin["name"],
                "old_password": "correct123",
                "password1": "new-pass-123",
                "password2": "new-pass-123",
            },
        )
        assert "Profile updated" in response

    def test_user_update_with_null_password(self, user):
        user["password"] = None

        with pytest.raises(tk.ValidationError):
            call_action("user_update", **user)

    def test_user_update_with_invalid_password(self, user):
        for password in (False, -1, 23, 30.7):
            user["password"] = password

            with pytest.raises(tk.ValidationError):
                call_action("user_update", **user)
