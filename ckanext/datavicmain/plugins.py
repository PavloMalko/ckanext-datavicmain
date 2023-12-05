# Plugins for ckanext-datavicmain
from __future__ import annotations

import time
import calendar
import logging
from typing import Any
from six import text_type
from typing import Any
from datetime import datetime

import ckan.authz as authz
import ckan.model as model
import ckan.plugins as p
import ckan.plugins.toolkit as toolkit

from ckanext.syndicate.interfaces import ISyndicate, Profile
from ckanext.oidc_pkce.interfaces import IOidcPkce
from ckanext.transmute.interfaces import ITransmute

from ckanext.datavicmain import helpers, cli
from ckanext.datavicmain.syndication.odp import prepare_package_for_odp
from ckanext.datavicmain.transmutators import get_transmutators


config = toolkit.config
request = toolkit.request
get_action = toolkit.get_action
log = logging.getLogger(__name__)
workflow_enabled = False

CONFIG_EXTRA_ALLOWED = "ckanext.datavicmain.extra_allowed_routes"

# Conditionally import the the workflow extension helpers if workflow extension enabled in .ini
if "workflow" in config.get('ckan.plugins', False):
    from ckanext.workflow import helpers as workflow_helpers
    workflow_enabled = True


def parse_date(date_str):
    try:
        return calendar.timegm(time.strptime(date_str, "%Y-%m-%d"))
    except Exception as e:
        return None


def release_date(pkg_dict):
    """
    Copied from https://github.com/salsadigitalauorg/datavic_ckan_2.2/blob/develop/iar/src/ckanext-datavic/ckanext/datavic/plugin.py#L296
    :param pkg_dict:
    :return:
    """
    dates = []
    dates.append(pkg_dict['metadata_created'])
    for resource in pkg_dict['resources']:
        if 'release_date' in resource and resource['release_date'] != '' and resource['release_date'] != '1970-01-01':
            dates.append(resource['release_date'])
    dates.sort()
    return dates[0].split("T")[0]


@toolkit.blanket.auth_functions
@toolkit.blanket.actions
@toolkit.blanket.validators
@toolkit.blanket.config_declarations
class DatasetForm(p.SingletonPlugin, toolkit.DefaultDatasetForm):
    ''' A plugin that provides some metadata fields and
    overrides the default dataset form
    '''
    p.implements(p.ITemplateHelpers)
    p.implements(p.IConfigurer, inherit=True)
    p.implements(p.IPackageController, inherit=True)
    p.implements(p.IBlueprint)
    p.implements(p.IClick)
    p.implements(ISyndicate, inherit=True)
    p.implements(IOidcPkce, inherit=True)
    p.implements(p.IAuthenticator, inherit=True)
    p.implements(p.IOrganizationController, inherit=True)
    p.implements(IOidcPkce, inherit=True)
    p.implements(ITransmute)


    # IBlueprint
    def get_blueprint(self):
        return helpers._register_blueprints()

    # IOidcPkce

    def oidc_login_response(self, user: model.User):

        if not toolkit.h.is_user_account_pending_review(user.id):
            return None

        toolkit.h.flash_success(toolkit._('Your requested account has been submitted for review'))
        return toolkit.h.redirect_to('home.index')

    @classmethod
    def organization_list_objects(cls, org_names=[]):
        ''' Make a action-api call to fetch the a list of full dict objects (for each organization) '''
        context = {
            'model': model,
            'session': model.Session,
            'user': toolkit.g.user,
        }

        options = {'all_fields': True}
        if org_names and len(org_names):
            t = type(org_names[0])
            if t is str:
                options['organizations'] = org_names
            elif t is dict:
                options['organizations'] = map(lambda org: org.get('name'), org_names)

        return get_action('organization_list')(context, options)

    @classmethod
    def organization_dict_objects(cls, org_names=[]):
        ''' Similar to organization_list_objects but returns a dict keyed to the organization name. '''
        results = {}
        for org in cls.organization_list_objects(org_names):
            results[org['name']] = org
        return results

    @classmethod
    def is_admin(cls, owner_org):
        if workflow_enabled:
            user = toolkit.g.userobj
            if authz.is_sysadmin(user.name):
                return True
            else:
                role = workflow_helpers.role_in_org(owner_org, user.name)
                if role == 'admin':
                    return True

    def is_sysadmin(self):
        user = toolkit.g.user
        if authz.is_sysadmin(user):
            return True

    def group_resources_by_temporal_range(
        self, resource_list: list[dict[str, Any]]
    ) -> list[list[dict[str, Any]]]:
        """Group resources by period_start/period_end dates for a historical
        feature."""

        def parse_date(date_str: str | None) -> datetime:
            return (
                datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.min
            )

        grouped_resources: dict[
            tuple[datetime], list[dict[str, Any]]
        ] = {}

        for resource in resource_list:
            end_date = parse_date(resource.get("period_end"))

            grouped_resources.setdefault((end_date,), [])
            grouped_resources[(end_date,)].append(resource)


        sorted_grouped_resources = dict(
            sorted(
                grouped_resources.items(),
                reverse=True,
                key=lambda x: x[0],
            )
        )

        return [res_group for res_group in sorted_grouped_resources.values()]

    def ungroup_temporal_resources(
        self, resource_groups: list[list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        return [
            resource for res_group in resource_groups for resource in res_group
        ]

    def is_historical(self):
        if toolkit.get_endpoint()[1] == 'historical':
            return True

    def get_formats(self, limit=100):
        try:
            # Get any additional formats added in the admin settings
            additional_formats = [x.strip() for x in config.get('ckan.datavic.authorised_resource_formats', []).split(',')]
            q = request.GET.get('q', '')
            list_of_formats = [x.encode('utf-8') for x in
                               get_action('format_autocomplete')({}, {'q': q, 'limit': limit}) if x] + additional_formats
            list_of_formats = sorted(list(set(list_of_formats)))
            dict_of_formats = []
            for item in list_of_formats:
                if item == ' ' or item == '':
                    continue
                else:
                    dict_of_formats.append({'value': item.lower(), 'text': item.upper()})
            dict_of_formats.insert(0, {'value': '', 'text': 'Please select'})

        except Exception as e:
            return []
        else:
            return dict_of_formats

    def repopulate_user_role(self):
        if 'submit' in request.args:
            return request.args['role']
        else:
            return 'member'

    ## ITemplateHelpers interface ##

    def get_helpers(self):
        ''' Return a dict of named helper functions (as defined in the ITemplateHelpers interface).
        These helpers will be available under the 'h' thread-local global object.
        '''
        return {
            'organization_list_objects': self.organization_list_objects,
            'organization_dict_objects': self.organization_dict_objects,
            'dataset_extra_fields': helpers.dataset_fields,
            'resource_extra_fields': helpers.resource_fields,
            'workflow_status_options': helpers.workflow_status_options,
            'is_admin': self.is_admin,
            'workflow_status_pretty': helpers.workflow_status_pretty,
            'group_resources_by_temporal_range': self.group_resources_by_temporal_range,
            'ungroup_temporal_resources': self.ungroup_temporal_resources,
            'is_historical': self.is_historical,
            'get_formats': self.get_formats,
            'is_sysadmin': self.is_sysadmin,
            'repopulate_user_role': self.repopulate_user_role,
            'group_list': helpers.group_list,
            'autoselect_workflow_status_option': helpers.autoselect_workflow_status_option,
            'release_date': release_date,
            'is_dataset_harvested': helpers.is_dataset_harvested,
            'is_user_account_pending_review': helpers.is_user_account_pending_review,
            'option_value_to_label': helpers.option_value_to_label,
            'field_choices': helpers.field_choices,
            'user_org_can_upload': helpers.user_org_can_upload,
            'is_ready_for_publish': helpers.is_ready_for_publish,
            'get_digital_twin_resources': helpers.get_digital_twin_resources,
            'url_for_dtv_config': helpers.url_for_dtv_config,
            "datavic_org_uploads_allowed": helpers.datavic_org_uploads_allowed,
        }

    ## IConfigurer interface ##
    def update_config_schema(self, schema):
        schema.update({
            'ckan.datavic.authorised_resource_formats': [
                toolkit.get_validator('ignore_missing'),
                toolkit.get_validator('unicode_safe'),

            ],
            'ckan.datavic.request_access_review_emails': [
                toolkit.get_validator('ignore_missing'),
                toolkit.get_validator('unicode_safe'),
            ]
        })

        return schema

    def update_config(self, config):
        ''' Setup the (fanstatic) resource library, public and template directory '''
        p.toolkit.add_public_directory(config, 'public')
        p.toolkit.add_template_directory(config, 'templates')
        p.toolkit.add_resource('public', 'ckanext-datavicmain')
        p.toolkit.add_resource('webassets', 'ckanext-datavicmain')
        p.toolkit.add_ckan_admin_tab(
            p.toolkit.config,
            'datavicmain.admin_report',
            'Admin Report',
            icon='user-o'
        )

    # IPackageController

    def after_dataset_create(self, context, pkg_dict):
        # Only add packages to groups when being created via the CKAN UI
        # (i.e. not during harvesting)
        if repr(toolkit.request) != '<LocalProxy unbound>' \
            and toolkit.get_endpoint()[0] in ['dataset', 'package', "datavic_dataset"]:
            # Add the package to the group ("category")
            pkg_group = pkg_dict.get('category', None)
            pkg_name = pkg_dict.get('name', None)
            pkg_type = pkg_dict.get('type', None)
            if pkg_group and pkg_type in ['dataset', 'package']:
                group = model.Group.get(pkg_group)
                group.add_package_by_name(pkg_name)
                # DATAVIC-251 - Create activity for private datasets
                helpers.set_private_activity(pkg_dict, context, str('new'))
        pass

    def after_dataset_update(self, context, pkg_dict):
        # Only add packages to groups when being updated via the CKAN UI
        # (i.e. not during harvesting)
        if repr(toolkit.request) != '<LocalProxy unbound>' \
            and toolkit.get_endpoint()[0] in ['dataset', 'package', "datavic_dataset"]:
            if 'type' in pkg_dict and pkg_dict['type'] in ['dataset', 'package']:
                helpers.add_package_to_group(pkg_dict, context)
                # DATAVIC-251 - Create activity for private datasets
                helpers.set_private_activity(pkg_dict, context, str('changed'))

    def before_dataset_index(self, pkg_dict: dict[str, Any]) -> dict[str, Any]:
        if pkg_dict.get("res_format"):
            pkg_dict["res_format"] = [
                res_format.upper().split(".")[-1]
                for res_format in pkg_dict["res_format"]
            ]

        if pkg_dict.get("res_format") and self._is_all_api_format(pkg_dict):
            pkg_dict.get("res_format").append("ALL_API")
        return pkg_dict

    def _is_all_api_format(self, pkg_dict: dict[str, Any]) -> bool:
        """Check if the dataset contains a resource in a format recognized as an API.
        This involves determining if the format of the resource is CSV and if this resource exists in the datastore
        or matches a format inside a predefined list.
        """
        for resource in toolkit.get_action("package_show")({"ignore_auth": True}, {"id": pkg_dict["id"]}).get(
                "resources", []):
            if resource["format"].upper() == "CSV" and resource["datastore_active"]:
                return True

        if [
            res_format
            for res_format in pkg_dict["res_format"]
            if res_format
            in [
                "WMS",
                "WFS",
                "API",
                "ARCGIS GEOSERVICES REST API",
                "ESRI REST",
                "GEOJSON",
            ]
        ]:
            return True
        return False
    
    # IClick
    def get_commands(self):
        return cli.get_commands()

    # ISyndicate
    def _requires_public_removal(self, pkg: model.Package, profile: Profile) -> bool:
        """Decide, whether the package must be deleted from Discover.
        """
        is_syndicated = bool(pkg.extras.get(profile.field_id))
        is_deleted = pkg.state == "deleted"
        is_archived = pkg.extras.get("workflow_status") == "archived"
        return is_syndicated and (is_deleted or is_archived)


    def prepare_package_for_syndication(self, package_id, data_dict, profile):
        if profile.id == "odp":
            data_dict = prepare_package_for_odp(package_id, data_dict)

        pkg = model.Package.get(package_id)
        assert pkg, f"Cannot syndicate non-existing package {package_id}"

        if self._requires_public_removal(pkg, profile):
            data_dict["state"] = "deleted"

        return data_dict

    def skip_syndication(
        self, package: model.Package, profile: Profile
    ) -> bool:
        if package.type == "harvest":
            log.debug("Do not syndicate %s because it is a harvest source", package.id)
            return True

        if self._requires_public_removal(package, profile):
            log.debug("Syndicate %s because it requires removal", package.id)
            return False

        if package.private:
            log.debug("Do not syndicate %s because it is private", package.id)
            return True

        if  'published' != package.extras.get("workflow_status"):
            log.debug("Do not syndicate %s because it is not published", package.id)
            return True

        return False

    # ITransmute

    def get_transmutators(self):
        return get_transmutators()
