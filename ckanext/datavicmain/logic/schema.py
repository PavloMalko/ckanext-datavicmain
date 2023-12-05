from __future__ import annotations

from typing import Any

from ckan.logic.schema import validator_args


@validator_args
def delwp_data_request_schema(
    not_missing, unicode_safe, email_validator, package_id_or_name_exists
) -> dict[str, list[Any]]:
    return {
        "username": [not_missing, unicode_safe],
        "email": [not_missing, unicode_safe, email_validator],
        "organisation": [not_missing, unicode_safe],
        "message": [not_missing, unicode_safe],
        "contact_email": [not_missing, unicode_safe, email_validator],
        "package_id": [not_missing, unicode_safe, package_id_or_name_exists],
    }
