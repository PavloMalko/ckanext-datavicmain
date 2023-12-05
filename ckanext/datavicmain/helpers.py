from __future__ import annotations

import os
import pkgutil
import inspect
import logging
import json
import base64
from typing import Any

from urllib.parse import urlsplit, urljoin

from flask import Blueprint

import ckan.model as model
import ckan.authz as authz
import ckan.plugins.toolkit as toolkit
import ckan.lib.mailer as mailer

from ckanext.harvest.model import HarvestObject
from ckanext.activity.model.activity import Activity
from . import utils

config = toolkit.config
request = toolkit.request
log = logging.getLogger(__name__)
WORKFLOW_STATUS_OPTIONS = ['draft', 'ready_for_approval', 'published', 'archived']

CONFIG_REGISTRATION_ENDPOINTS = "ckanext.datavicmain.registration_endpoints"
DEFAULT_REGISTRATION_ENDPOINTS = ["user.register", "datavicuser.register"]

CONFIG_DTV_FQ = "ckanext.datavicmain.dtv.supported_formats"
DEFAULT_DTV_FQ = [
    "wms", "shapefile", "zip (shp)", "shp", "kmz",
    "geojson", "csv-geo-au", "aus-geo-csv"
]

# Conditionally import the the workflow extension helpers if workflow extension enabled in .ini
if "workflow" in config.get('ckan.plugins', False):
    from ckanext.workflow import helpers as workflow_helpers
    workflow_enabled = True


def add_package_to_group(pkg_dict, context):
    group_id = pkg_dict.get('category', None)
    if group_id:
        group = model.Group.get(group_id)
        groups = context.get('package').get_groups('group')
        if group not in groups:
            group.add_package_by_name(pkg_dict.get('name'))


def set_data_owner(owner_org):
    data_owner = ''
    if owner_org:
        organization = model.Group.get(owner_org)
        if organization:
            parents = organization.get_parent_group_hierarchy('organization')
            if parents:
                data_owner = parents[0].title
            else:
                data_owner = organization.title
    return data_owner.strip()


def is_dataset_harvested(package_id):
    if not package_id:
        return None

    harvested = model.Session.query(model.Session.query(HarvestObject).filter_by(package_id=package_id).filter_by(state='COMPLETE').exists()).scalar()

    return harvested


def is_user_account_pending_review(user_id):
    # get_action('user_show') does not return the 'reset_key' so the only way to get this field is from the User model
    user = model.User.get(user_id)
    return user and user.is_pending() and user.reset_key is None


def send_email(user_emails, email_type, extra_vars):
    if not user_emails or len(user_emails) == 0:
        return

    subject = toolkit.render('emails/subjects/{0}.txt'.format(email_type), extra_vars)
    body = toolkit.render('emails/bodies/{0}.txt'.format(email_type), extra_vars)
    for user_email in user_emails:
        try:
            log.debug('Attempting to send {0} to: {1}'.format(email_type, user_email))
            # Attempt to send mail.
            mail_dict = {
                'recipient_name': user_email,
                'recipient_email': user_email,
                'subject': subject,
                'body': body
            }
            mailer.mail_recipient(**mail_dict)
        except (mailer.MailerException) as ex:
            log.error(u'Failed to send email {email_type} to {user_email}.'.format(email_type=email_type, user_email=user_email))
            log.error('Error: {ex}'.format(ex=ex))


def set_private_activity(pkg_dict, context, activity_type):
    pkg = model.Package.get(pkg_dict['id'])
    user = context['user']
    session = context['session']
    user_obj = model.User.by_name(user)

    if user_obj:
        user_id = user_obj.id
    else:
        user_id = str('not logged in')

    activity = Activity.activity_stream_item(pkg, activity_type, user_id)
    session.add(activity)
    return pkg_dict


def user_is_registering():
    return toolkit.get_endpoint() == ("datavicuser", "register")


def _register_blueprints():
    u'''Return all blueprints defined in the `views` folder
    '''
    blueprints = []

    def is_blueprint(mm):
        return isinstance(mm, Blueprint)

    path = os.path.join(os.path.dirname(__file__), 'views')

    for loader, name, _ in pkgutil.iter_modules([path]):
        module = loader.find_module(name).load_module(name)
        for blueprint in inspect.getmembers(module, is_blueprint):
            blueprints.append(blueprint[1])
            log.info(u'Registered blueprint: {0!r}'.format(blueprint[0]))
    return blueprints


def dataset_fields(dataset_type='dataset'):
    schema = toolkit.h.scheming_get_dataset_schema(dataset_type)
    return schema.get('dataset_fields', [])


def resource_fields(dataset_type='dataset'):
    schema = toolkit.h.scheming_get_dataset_schema(dataset_type)
    return schema.get('resource_fields', [])


def field_choices(field_name):
    field = toolkit.h.scheming_field_by_name(dataset_fields(), field_name)
    return toolkit.h.scheming_field_choices(field)


def option_value_to_label(field_name, value):
    choices = field_choices(field_name)
    label = toolkit.h.scheming_choices_label(
        choices,
        value)

    return label


def group_list(self):
    group_list = []
    for group in model.Group.all('group'):
        group_list.append({'value': group.id, 'label': group.title})
    return group_list


def workflow_status_options(current_workflow_status, owner_org):
    options = []
    if "workflow" in config.get('ckan.plugins', False):
        user = toolkit.g.user

        #log1.debug("\n\n\n*** workflow_status_options | current_workflow_status: %s | owner_org: %s | user: %s ***\n\n\n", current_workflow_status, owner_org, user)
        for option in workflow_helpers.get_available_workflow_statuses(current_workflow_status, owner_org, user):
            options.append({'value': option, 'text': option.replace('_', ' ').capitalize()})

        return options
    else:
        return [{'value': 'draft', 'text': 'Draft'}]


def autoselect_workflow_status_option(current_workflow_status):
    selected_option = 'draft'
    user = toolkit.g.user
    if authz.is_sysadmin(user):
        selected_option = current_workflow_status
    return selected_option


def workflow_status_pretty(workflow_status):
    return workflow_status.replace('_', ' ').capitalize()


def get_organisations_allowed_to_upload_resources():
    orgs = toolkit.config.get('ckan.organisations_allowed_to_upload_resources', ['victorian-state-budget'])
    return orgs


def get_user_organizations(username):
    user = model.User.get(username)
    return user.get_groups('organization')


def user_org_can_upload(pkg_id):
    user = toolkit.g.user
    context = {'user': user}
    org_name = None
    if pkg_id is None:
        request_path = urlsplit(request.url)
        if request_path.path is not None:
            fragments = request_path.path.split('/')
            if fragments[1] == 'dataset':
                pkg_id = fragments[2]

    if pkg_id is not None:
        dataset = toolkit.get_action('package_show')(context, {'name_or_id': pkg_id})
        org_name = dataset.get('organization').get('name')


    if toolkit.h.datavic_org_uploads_allowed(org_name) and (
            authz.users_role_for_group_or_org(org_name, user) in ["editor", "admin"]
    ):
        return True

    allowed_organisations = get_organisations_allowed_to_upload_resources()
    user_orgs = get_user_organizations(user)
    for org in user_orgs:
        if org.name in allowed_organisations and org.name == org_name:
            return True
    return False


def is_ready_for_publish(pkg):
    workflow_publish = pkg.get('workflow_status')
    is_private = pkg.get('private')

    if not is_private and workflow_publish == 'published':
        return True
    return False


def get_digital_twin_resources(pkg_id: str) -> list[dict[str, Any]]:
    """Select resource suitable for DTV(Digital Twin Visualization).

    Additional info:
    https://gist.github.com/steve9164/b9781b517c99486624c02fdc7af0f186
    """
    supported_formats = {
        fmt.lower() for fmt in
        toolkit.aslist(toolkit.config.get(CONFIG_DTV_FQ, DEFAULT_DTV_FQ))
    }

    try:
        pkg = toolkit.get_action("package_show")({}, {"id": pkg_id})
    except (toolkit.ObjectNotFound, toolkit.NotAuthorized):
        return []

    # Additional info #2
    if pkg["state"] != "active":
        return []

    acceptable_resources = {}
    for res in pkg["resources"]:
        if not res["format"]:
            continue

        fmt = res["format"].lower()
        # Additional info #1
        if fmt not in supported_formats:
            continue

        # Additional info #3
        if fmt in {"kml", "kmz", "shp", "shapefile", "zip (shp)"} and len(
                pkg["resources"]
        ) > 1:
            continue

        # Additional info #3
        if fmt == "wms" and ~res["url"].find("data.gov.au/geoserver"):
            continue

        # Additional info #4
        if res["name"] in acceptable_resources:
            if acceptable_resources[res["name"]]["created"] > res["created"]:
                continue

        acceptable_resources[res["name"]] = res

    return list(acceptable_resources.values())


def url_for_dtv_config(ids: list[str], embedded: bool = True) -> str:
    """Build URL where DigitalTwin can get map configuration for the preview.

    It uses ODP base URL because DigitalTwin doesn't have access to IAR. As
    result, non-syndicated datasets cannot be visualized.

    """
    base_url: str = (
        toolkit.config.get("ckanext.datavicmain.odp.public_url")
        or toolkit.config["ckan.site_url"]
    )

    encoded = base64.urlsafe_b64encode(bytes(json.dumps(ids), "utf8"))
    return urljoin(
        base_url,
        toolkit.url_for("datavicmain.dtv_config", encoded=encoded, embedded=embedded)
    )


def datavic_org_uploads_allowed(org_id: str) -> bool:
    org = model.Group.get(org_id)
    if not org:
        return False

    try:
        flake = toolkit.get_action("flakes_flake_lookup")({"ignore_auth": True}, {
            "author_id": None,
            "name": utils.org_uploads_flake_name(),
        })
    except toolkit.ObjectNotFound:
        return False

    return flake["data"].get(org.id, False)
