from __future__ import annotations

import csv
import datetime
import logging
import openpyxl
import mimetypes
from itertools import groupby
from os import path
from typing import Any
from urllib.parse import urlparse

import click
import tqdm
from ckanext.harvest.model import HarvestObject, HarvestSource
from sqlalchemy.orm import Query

import ckan.logic.validators as validators
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckan.lib.munge import munge_title_to_name
from ckan.model import Resource, ResourceView
from ckan.types import Context

log = logging.getLogger(__name__)

IDX_ID = 0
IDX_NAME = 1
IDX_TITLE = 2
IDX_STATE = 3
NAME_FIELD_LENGTH = 99

XLSX_IDX_TITLE = 0
XLSX_IDX_CURRENT_URL = 5
XLSX_IDX_NEW_URL = 6


@click.group()
def maintain():
    """Portal maintenance tasks"""
    pass


@maintain.command("ckan-resources-date-cleanup")
def ckan_iar_resource_date_cleanup():
    """Fix resources with invalid date range. One-time task."""
    user = tk.get_action("get_site_user")({"ignore_auth": True}, {})

    limit = 100
    offset = 0
    packages_found = True

    while packages_found:
        package_list = tk.get_action("current_package_list_with_resources")(
            {"user": user["name"]}, {"limit": limit, "offset": offset}
        )
        if len(package_list) == 0:
            packages_found = False
        offset += 1

        for package in package_list:
            fix_available = False
            click.secho(
                f"Processing resources in {package['name']}", fg="green"
            )

            for resource in package.get("resources"):
                if _fix_improper_date_values(resource):
                    fix_available = True

            if not fix_available:
                continue
            try:
                tk.get_action("package_patch")(
                    {"user": user["name"]},
                    {"id": package["id"], "resources": package["resources"]},
                )
                click.secho(
                    f"Fixed date issues for resources in {package['name']}",
                    fg="green",
                )
            except tk.ValidationError as e:
                click.secho(
                    f"Failed to fix  resources {package['name']}: {e}",
                    fg="red",
                )


def _fix_improper_date_values(resource: dict[str, Any]) -> bool:
    """Make the invalid date field value to None.


    Args:
        resource (dict) : resource data.


    Returns:
        bool: True if date values updated.
    """
    date_fields = ["period_end", "period_start", "release_date"]

    old_resource = resource.copy()

    for field in date_fields:
        if not resource.get(field):
            continue
        if not _valid_date(resource[field]):
            click.secho(
                f"Found invalid date for {field} in {resource['name']}:"
                f" {resource[field]}",
                fg="red",
            )
            resource[field] = None

    return old_resource != resource


def _valid_date(date: str) -> bool:
    """Validates given date.


    Args:
        date (str): date in YY-MM-DD format.


    Returns:
        bool: True if date is valid.
    """
    date_format = "%Y-%m-%d"
    try:
        datetime.datetime.strptime(date, date_format)
    except ValueError:
        return False

    return True


@maintain.command()
def drop_wms_records():
    """Purge old WMS records. One-time script"""

    file = open(path.join(path.dirname(__file__), "data/old_wms_records.csv"))
    csv_reader = csv.DictReader(file)

    for row in csv_reader:
        dataset_name: str = row["url"].split("/")[-1]

        try:
            tk.get_action("dataset_purge")(
                {"ignore_auth": True}, {"id": dataset_name}
            )
        except tk.ObjectNotFound as e:
            click.secho(
                f"Error purging <{dataset_name}> dataset: {e}", fg="red"
            )
        else:
            click.secho(
                f"Dataset <{dataset_name}> has been purged", fg="green"
            )

    file.close()


@maintain.command("recline-to-datatable")
@click.option("-d", "--delete", is_flag=True, help="Delete recline_view views")
def replace_recline_with_datatables(delete: bool):
    """Replaces recline_view with datatables_view
    Args:
        delete (bool): delete existing `recline_view` views
    """
    resources = [
        res
        for res in model.Session.query(Resource).all()
        if res.extras.get("datastore_active")
    ]
    if not resources:
        click.secho("No resources have been found", fg="green")
        return click.secho(
            "NOTE: `datatables_view` works only with resources uploaded to"
            " datastore",
            fg="green",
        )
    click.secho(
        f"{len(resources)} resources have been found. Updating views...",
        fg="green",
    )
    with tqdm.tqdm(resources) as bar:
        for res in bar:
            res_views = _get_existing_views(res.id)
            if not _is_datatable_view_exist(res_views):
                _create_datatable_view(res.id)
            if delete:
                _delete_recline_views(res_views)


def _get_existing_views(resource_id: str) -> list[ResourceView]:
    """Returns a list of resource view entities
    Args:
        resource_id (str): resource ID
    Returns:
        list[ResourceView]: list of resource views
    """
    return (
        model.Session.query(ResourceView)
        .filter(ResourceView.resource_id == resource_id)
        .all()
    )


def _is_datatable_view_exist(res_views: list[ResourceView]) -> bool:
    """Checks if at least one view from resource views is `datatables_view`
    Args:
        res_views (list[ResourceView]): list of resource views
    Returns:
        bool: True if `datatables_view` view exists
    """
    for view in res_views:
        if view.view_type == "datatables_view":
            return True
    return False


def _create_datatable_view(resource_id: str):
    """Creates a datatable view for resource
    Args:
        resource_id (str): resource ID
    """
    tk.get_action("resource_view_create")(
        {"ignore_auth": True},
        {
            "resource_id": resource_id,
            "show_fields": _get_resource_fields(resource_id),
            "title": "Datatable",
            "view_type": "datatables_view",
        },
    )


def _get_resource_fields(resource_id: str) -> list[str]:
    """Fetches list of resource fields from datastore
    Args:
        resource_id (str): resource ID
    Returns:
        list[str]: list of resource fields
    """
    ctx = {"ignore_auth": True}
    data_dict = {
        "resource_id": resource_id,
        "limit": 0,
        "include_total": False,
    }
    try:
        search = tk.get_action("datastore_search")(ctx, data_dict)
    except tk.ObjectNotFound:
        click.echo(f"Resource {resource_id} orphaned")
        return []

    fields = [field for field in search["fields"]]
    return [f["id"] for f in fields]


def _delete_recline_views(res_views: list[ResourceView]):
    for view in res_views:
        if view.view_type != "recline_view":
            continue
        view.delete()
    model.repo.commit()


@maintain.command(
    "purge-delwp-duplicates", short_help="Purge duplicates of DELWP datasets"
)
def purge_delwp_duplicates():
    """
    Purge all duplicates of DELWP datasets and rename them in order to match
    their names with titles
    """

    click.secho("Searching for duplicated DELWP datasets...")

    taken_names = [
        name[0] for name in model.Session.query(model.Package.name).all()
    ]

    query = _get_query_delwp_datasets()
    datasets = (
        query.with_entities(
            model.Package.id,
            model.Package.name,
            model.Package.title,
            model.Package.state,
        )
        .distinct()
        .order_by(model.Package.title)
        .all()
    )

    click.secho(
        f"{len(datasets)} DELWP datasets have been found.",
        fg="green",
    )
    click.secho("Purging duplicates and renaming datasets...", fg="green")

    counter_purged = 0
    counter_renamed = 0
    unchanged_pkgs = []
    for key, grp in groupby(datasets, lambda x: x[IDX_TITLE]):
        pkgs = [dataset for dataset in grp]
        pkgs_sorted = sorted(pkgs, key=lambda x: x[IDX_NAME], reverse=False)
        pkgs_len = len(pkgs_sorted)

        if pkgs_len < 2:
            continue

        for idx, pkg in enumerate(pkgs_sorted):
            if (idx == pkgs_len - 1) and (pkg[IDX_STATE] == "active"):
                # Renaming datasets (names match with titles)
                pkg_obj = model.Session.query(model.Package).get(pkg[IDX_ID])
                cur_name = pkg_obj.name
                new_name = munge_title_to_name(pkg_obj.title)
                if (
                    new_name in taken_names
                    or len(pkg.title) > NAME_FIELD_LENGTH
                ):
                    click.secho(
                        f"Dataset <{pkg_obj.title}> with the name"
                        f" <{cur_name}>: Couldn't generate the unique name"
                        f" {new_name} from the title.",
                        fg="red",
                    )
                    unchanged_pkgs.append(pkg_obj.title)
                    continue
                pkg_obj.name = new_name
                click.secho(
                    f"Renamed: from <{cur_name}> to <{pkg_obj.name}>",
                    fg="green",
                )
                counter_renamed += 1
            else:
                # Purging duplicates of datasets from DB entirely
                site_user = tk.get_action("get_site_user")(
                    {"ignore_auth": True}, {}
                )
                context: Context = {
                    "user": site_user["name"],
                    "ignore_auth": True,
                }
                try:
                    tk.get_action("dataset_purge")(
                        context, {"id": pkg[IDX_ID]}
                    )
                except tk.ObjectNotFound as e:
                    click.secho(
                        "Purging ERROR occurred in the dataset"
                        f" <{pkg[IDX_ID]}>: {e}",
                        fg="red",
                    )
                else:
                    taken_names.remove(pkg[IDX_NAME])
                    click.secho(
                        f"Purged: {pkg[IDX_TITLE]} - ID: {pkg[IDX_ID]}",
                        fg="yellow",
                    )
                    counter_purged += 1

    model.Session.commit()

    click.secho("Done.", fg="green")
    click.secho(f"{counter_purged} DELWP datasets - purged.", fg="green")
    click.secho(f"{counter_renamed} DELWP datasets - renamed.", fg="green")
    click.secho(
        f"{len(unchanged_pkgs)} DELWP datasets - unchanged: ", fg="yellow"
    )
    click.secho(f"{unchanged_pkgs}", fg="yellow")


@maintain.command(
    "list-delwp-wrong-names", short_help="List DELWP datasets with wrong names"
)
def list_delwp_wrong_names():
    """
    Display a list of DELWP datasets with active state and wrong names
    which do not correspond their titles
    """

    click.secho("Searching for DELWP datasets...")

    query = _get_query_delwp_datasets()
    datasets = (
        query.filter(model.Package.state == model.State.ACTIVE)
        .with_entities(
            model.Package.id, model.Package.name, model.Package.title
        )
        .distinct()
        .order_by(model.Package.title)
        .all()
    )

    click.secho(
        f"{len(datasets)} DELWP datasets have been found.",
        fg="green",
    )

    counter = 0
    for dataset in datasets:
        pkg = model.Session.query(model.Package).get(dataset[IDX_ID])
        cur_name = pkg.name
        new_name = munge_title_to_name(pkg.title)
        if cur_name != new_name:
            counter += 1
            click.secho(
                f"{dataset[IDX_TITLE]} - {dataset[IDX_NAME]}", fg="yellow"
            )

    click.secho(
        f"{counter} active DELWP datasets with wrong names.", fg="green"
    )


def _get_query_delwp_datasets() -> Query[model.Package]:
    """Get all DELWP datsets

    Returns:
        Query[model.Package]: Package model query object
    """
    return (
        model.Session.query(model.Package)
        .join(HarvestObject, model.Package.id == HarvestObject.package_id)
        .join(
            HarvestSource, HarvestObject.harvest_source_id == HarvestSource.id
        )
        .filter(HarvestSource.type == "delwp")
    )


@maintain.command("get-broken-recline")
def identify_resources_with_broken_recline():
    """Return a list of resources with a broken recline_view"""

    query = (
        model.Session.query(model.Resource)
        .join(
            model.ResourceView,
            model.ResourceView.resource_id == model.Resource.id,
        )
        .filter(
            model.ResourceView.view_type.in_(
                ["datatables_view", "recline_view"]
            )
        )
    )

    resources = [resource for resource in query.all()]

    if not resources:
        return click.secho("No resources with inactive datastore")

    for resource in resources:
        if resource.extras.get("datastore_active"):
            continue

        res_url = tk.url_for(
            "resource.read",
            id=resource.package_id,
            resource_id=resource.id,
            _external=True,
        )
        click.secho(
            f"Resource {res_url} has a table view but datastore is inactive",
            fg="green",
        )


@maintain.command(u"update-broken-urls",
                  short_help=u"Update resources with broken urls")
def update_broken_urls():
    """Change resources urls' protocols from http to https listed in XLSX file"""

    file = path.join(
        path.dirname(__file__), "data/DTF Content list bulk URL change 20231017.xlsx"
    )
    wb = openpyxl.load_workbook(file)
    ws = wb.active

    for row in ws.iter_rows(min_row=2):
        title = row[XLSX_IDX_TITLE].value
        url = row[XLSX_IDX_CURRENT_URL].value

        resource = (
            model.Session.query(model.Resource)
            .filter(model.Resource.url == url)
            .first()
        )

        if not resource:
            click.secho(
                f"Resource <{title}> with URL <{url}> does not exist",
                fg="red"
            )
            continue

        resource.url = row[XLSX_IDX_NEW_URL].value
        click.secho(
            f"URL of resource <{title}> has been updated to <{resource.url}>",
            fg="green"
        )

        model.Session.commit()


@maintain.command("ckan-resources-format-fix")
def ckan_iar_resources_format_fix():
    """Fix resources with empty format field."""

    resources = model.Session.query(Resource).filter(model.Resource.format == "").all()

    if not resources:
        return click.secho("No resources with empty format", fg="green")

    for resource in resources:
        resource.format = _suggest_file_format(resource.url)

        click.secho(
            f"Resource '{resource.name}' changed format to: {resource.format}",
            fg="green",
        )
    model.Session.commit()
    click.secho(
        f"All formats was corrected.",
        fg="green",
    )


def _suggest_file_format(url: str | None) -> str:
    if not url:
        return "unknown"

    parsed = urlparse(url)
    if parsed.scheme and not parsed.path:
        return "unknown"

    mimetype, _ = mimetypes.guess_type(url)
    return validators.clean_format(mimetype) if mimetype else "unknown"
